"""
Vision feature extraction (classical CV / small on-device ML -- no LLM, no
cloud call, frames never leave the process).

One ``VisionExtractor`` is shared by all sessions in a worker process and owns
a small pool of MediaPipe / OpenCV model instances (the task objects are not
thread-safe, so each call borrows one).
"""
from __future__ import annotations

import logging
import math
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..framework.detector import FeatureExtractor
from ..framework.types import Frame
from . import assets
from .imaging import frame_stats, thumbnail

logger = logging.getLogger(__name__)

OBJECT_LABELS = {'person', 'cell phone', 'book', 'laptop', 'tv'}

# MediaPipe face-mesh landmark ids
_IRIS_A, _IRIS_B = 468, 473
_NOSE_TIP = 1
_MOUTH_A, _MOUTH_B = 61, 291


@dataclass
class FaceObs:
    bbox: tuple[float, float, float, float]      # normalised x0,y0,x1,y1
    area: float                                  # normalised bbox area
    yaw: float                                   # degrees; + = face turned toward image-right
    pitch: float                                 # degrees; + = face tilted up
    roll: float
    gaze_h: float                                # -1..1 ; + = eyes toward image-right
    gaze_v: float                                # -1..1 ; + = eyes up
    blink: float                                 # 0..1 mean eyelid closure
    mouth: float                                 # 0..1 jaw/lip activity composite
    landmarks: np.ndarray = field(repr=False, default=None)   # (478,3) normalised

    @property
    def center(self) -> tuple[float, float]:
        return ((self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2)


@dataclass
class ObjectObs:
    label: str
    score: float
    bbox: tuple[float, float, float, float]      # normalised
    area: float


def euler_from_matrix(m: np.ndarray) -> tuple[float, float, float]:
    """MediaPipe facial transformation matrix -> (yaw, pitch, roll) degrees.

    The matrix is in an OpenGL-style camera frame (x = image-right, y = up,
    camera looks down -z; verified by the roll test in tests/test_vision_models.py).
    ``n = R[:, 2]`` is the direction the face points. Therefore:
    yaw > 0  <=> face turned toward image-right,  pitch > 0 <=> face tilted up,
    roll > 0 <=> counter-clockwise in the image.
    """
    r = np.asarray(m, dtype=np.float64)[:3, :3]
    r = r / (np.linalg.norm(r, axis=0, keepdims=True) + 1e-9)
    yaw = math.degrees(math.atan2(r[0, 2], r[2, 2]))
    pitch = math.degrees(math.atan2(r[1, 2], math.hypot(r[0, 2], r[2, 2])))
    roll = math.degrees(math.atan2(r[1, 0], r[1, 1]))
    return yaw, pitch, roll


def _blend(bs: dict, *names) -> float:
    return float(np.mean([bs.get(n, 0.0) for n in names]))


class _Worker:
    """One set of model instances (borrowed by one thread at a time)."""

    def __init__(self, model_dir, use_objects: bool, use_embedding: bool):
        import mediapipe as mp
        self.mp = mp
        vision = mp.tasks.vision
        base = mp.tasks.BaseOptions
        fl = assets.model_path(model_dir, 'face_landmarker')
        if not fl.exists():
            raise FileNotFoundError(f'{fl} missing - run `manage.py fetch_proctor_models`')
        self.landmarker = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=base(model_asset_path=str(fl)),
            running_mode=vision.RunningMode.IMAGE, num_faces=4,
            min_face_detection_confidence=0.5, min_face_presence_confidence=0.5,
            output_face_blendshapes=True, output_facial_transformation_matrixes=True))
        self.detector = None
        od = assets.model_path(model_dir, 'object_detector')
        if use_objects and od.exists():
            self.detector = vision.ObjectDetector.create_from_options(vision.ObjectDetectorOptions(
                base_options=base(model_asset_path=str(od)), running_mode=vision.RunningMode.IMAGE,
                score_threshold=0.35, max_results=12))
        self.recognizer = None
        em = assets.model_path(model_dir, 'face_embedder')
        if use_embedding and em.exists():
            import cv2
            self.recognizer = cv2.FaceRecognizerSF.create(str(em), '')

    def close(self):
        for obj in (self.landmarker, self.detector):
            try:
                if obj is not None:
                    obj.close()
            except Exception:  # pragma: no cover
                pass
        self.landmarker = self.detector = self.recognizer = None


class VisionExtractor(FeatureExtractor):
    name = 'vision'
    provides = frozenset({'meta', 'faces', 'n_faces', 'primary', 'objects', 'thumb', 'embedding'})

    def __init__(self, model_dir: str | Path, pool_size: int = 2, use_objects: bool = True,
                 use_embedding: bool = True):
        self.model_dir = Path(model_dir)
        self.pool_size = pool_size
        self.use_objects = use_objects
        self.use_embedding = use_embedding
        self._pool: queue.Queue[_Worker] = queue.Queue()
        self._created = 0
        self._lock = threading.Lock()
        self._closed = False

    # -- pool ---------------------------------------------------------------
    def _borrow(self) -> _Worker:
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._created < self.pool_size:
                self._created += 1
                try:
                    return _Worker(self.model_dir, self.use_objects, self.use_embedding)
                except Exception:
                    self._created -= 1
                    raise
        return self._pool.get(timeout=5)

    def _release(self, w: _Worker):
        if self._closed:
            w.close()
        else:
            self._pool.put(w)

    def capabilities(self) -> dict[str, bool]:
        av = assets.available(self.model_dir)
        return {'faces': av['face_landmarker'], 'objects': self.use_objects and av['object_detector'],
                'identity': self.use_embedding and av['face_embedder']}

    def close(self):
        self._closed = True
        while True:
            try:
                self._pool.get_nowait().close()
            except queue.Empty:
                break

    # -- extraction -----------------------------------------------------------
    def extract(self, frame: Frame) -> dict[str, Any]:
        import cv2
        img = frame.image
        feats: dict[str, Any] = {'meta': frame_stats(img), 'thumb': thumbnail(img)}
        rgb = np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        w = self._borrow()
        try:
            mp_img = w.mp.Image(image_format=w.mp.ImageFormat.SRGB, data=rgb)
            res = w.landmarker.detect(mp_img)
            faces = [self._face(res, i, img.shape) for i in range(len(res.face_landmarks))]
            faces.sort(key=lambda f: f.area, reverse=True)
            feats['faces'] = faces
            feats['n_faces'] = len(faces)
            feats['primary'] = faces[0] if faces else None

            feats['objects'] = []
            feats['objects_ran'] = False
            if w.detector is not None and frame.hints.get('want_objects', True):
                od = w.detector.detect(mp_img)
                feats['objects_ran'] = True
                for d in od.detections:
                    cat = d.categories[0]
                    if cat.category_name not in OBJECT_LABELS:
                        continue
                    bb = d.bounding_box
                    H, W = img.shape[:2]
                    box = (bb.origin_x / W, bb.origin_y / H, (bb.origin_x + bb.width) / W, (bb.origin_y + bb.height) / H)
                    feats['objects'].append(ObjectObs(cat.category_name, float(cat.score), box,
                                                      float(bb.width * bb.height) / float(W * H)))

            feats['embedding'] = None
            if (w.recognizer is not None and faces and frame.hints.get('want_embedding')):
                feats['embedding'] = self._embed(w.recognizer, img, faces[0])
        finally:
            self._release(w)
        return feats

    @staticmethod
    def _face(res, i: int, shape) -> FaceObs:
        lm = np.array([[p.x, p.y, p.z] for p in res.face_landmarks[i]], dtype=np.float32)
        bs = {b.category_name: b.score for b in res.face_blendshapes[i]} if res.face_blendshapes else {}
        yaw, pitch, roll = euler_from_matrix(res.facial_transformation_matrixes[i])
        x0, y0 = np.clip(lm[:, :2].min(0), 0, 1)
        x1, y1 = np.clip(lm[:, :2].max(0), 0, 1)
        gaze_h = (_blend(bs, 'eyeLookOutLeft', 'eyeLookInRight') - _blend(bs, 'eyeLookOutRight', 'eyeLookInLeft'))
        gaze_v = (_blend(bs, 'eyeLookUpLeft', 'eyeLookUpRight') - _blend(bs, 'eyeLookDownLeft', 'eyeLookDownRight'))
        mouth = float(np.clip(bs.get('jawOpen', 0) + 0.5 * bs.get('mouthFunnel', 0)
                              + 0.5 * bs.get('mouthPucker', 0)
                              + 0.5 * _blend(bs, 'mouthLowerDownLeft', 'mouthLowerDownRight'), 0, 1.5))
        return FaceObs(bbox=(float(x0), float(y0), float(x1), float(y1)),
                       area=float((x1 - x0) * (y1 - y0)), yaw=yaw, pitch=pitch, roll=roll,
                       gaze_h=float(gaze_h), gaze_v=float(gaze_v),
                       blink=_blend(bs, 'eyeBlinkLeft', 'eyeBlinkRight'), mouth=mouth, landmarks=lm)

    @staticmethod
    def _embed(recognizer, img, face: FaceObs) -> np.ndarray | None:
        H, W = img.shape[:2]
        lm = face.landmarks
        eyes = sorted([(lm[_IRIS_A, 0] * W, lm[_IRIS_A, 1] * H), (lm[_IRIS_B, 0] * W, lm[_IRIS_B, 1] * H)])
        mouth = sorted([(lm[_MOUTH_A, 0] * W, lm[_MOUTH_A, 1] * H), (lm[_MOUTH_B, 0] * W, lm[_MOUTH_B, 1] * H)])
        x0, y0, x1, y1 = face.bbox
        row = np.array([[x0 * W, y0 * H, (x1 - x0) * W, (y1 - y0) * H,
                         *eyes[0], *eyes[1], lm[_NOSE_TIP, 0] * W, lm[_NOSE_TIP, 1] * H,
                         *mouth[0], *mouth[1], 1.0]], dtype=np.float32)
        try:
            aligned = recognizer.alignCrop(img, row)
            f = recognizer.feature(aligned).astype(np.float32).ravel()
        except Exception:  # pragma: no cover - cv2 raises on degenerate boxes
            logger.debug('embedding failed', exc_info=True)
            return None
        n = np.linalg.norm(f)
        return f / n if n > 0 else None
