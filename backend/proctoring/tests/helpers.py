"""Synthetic features so detector logic is tested without any ML model."""
from __future__ import annotations

import numpy as np

from proctoring.framework.detector import FrameContext
from proctoring.framework.types import Frame
from proctoring.vision import attention
from proctoring.vision.baseline import Baseline
from proctoring.vision.engine import FaceObs, ObjectObs


def face(yaw=0.0, pitch=0.0, gaze_h=0.0, gaze_v=0.0, blink=0.05, mouth=0.02, area=0.12,
         center=(0.5, 0.5), roll=0.0):
    half = (area ** 0.5) / 2
    cx, cy = center
    return FaceObs(bbox=(cx - half, cy - half, cx + half, cy + half), area=area, yaw=yaw, pitch=pitch,
                   roll=roll, gaze_h=gaze_h, gaze_v=gaze_v, blink=blink, mouth=mouth, landmarks=None)


def ready_baseline():
    b = Baseline(duration_s=1.0, min_samples=1)
    b.values = {'yaw': 0.0, 'pitch': 0.0, 'gaze_h': 0.0, 'gaze_v': 0.0, 'area': 0.12}
    b.spread = {k: 0.0 for k in b.values}
    b.ready = True
    return b


def ctx(ts, faces=None, objects=None, objects_ran=False, meta=None, thumb=None, embedding=None, baseline=None,
        client_ts=None):
    faces = faces if faces is not None else [face()]
    meta = meta or {'width': 640, 'height': 480, 'brightness': 120.0, 'contrast': 50.0, 'sharpness': 200.0}
    feats = {'meta': meta, 'faces': faces, 'n_faces': len(faces), 'primary': faces[0] if faces else None,
             'objects': objects or [], 'objects_ran': objects_ran,
             'thumb': thumb if thumb is not None else np.full((24, 24), 100.0, np.float32),
             'embedding': embedding}
    baseline = baseline or ready_baseline()
    if feats['primary'] is not None:
        feats['attention'] = attention.derive(feats['primary'], baseline)
    return FrameContext(frame=Frame(image=None, ts=ts, client_ts=client_ts), features=feats, baseline=baseline.values)


def obj(label, score=0.9, area=0.02):
    return ObjectObs(label=label, score=score, bbox=(0.1, 0.1, 0.3, 0.3), area=area)


def run(detector, contexts):
    out = []
    for c in contexts:
        out.extend(detector.process(c))
    return out
