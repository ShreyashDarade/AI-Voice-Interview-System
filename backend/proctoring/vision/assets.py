"""
Model asset registry. Weights are never committed; ``manage.py fetch_proctor_models``
downloads them and verifies the SHA-256 pins below.

Licences (verify again before commercial launch -- see docs/COMPLIANCE.md):
  * face_landmarker.task      Google MediaPipe model bundle, Apache-2.0 terms
  * efficientdet_lite0.tflite Google MediaPipe / TF model zoo, Apache-2.0
  * sface.onnx                OpenCV Zoo SFace, Apache-2.0 (training-data provenance
                              is discussed in opencv_zoo issues #313/#318 -> legal review)
Deliberately NOT used: Ultralytics YOLO (AGPL-3.0) and InsightFace pretrained
weights (non-commercial).
"""
from __future__ import annotations

import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelSpec:
    key: str
    filename: str
    url: str
    sha256: str
    purpose: str
    required: bool = True


MODEL_SPECS: dict[str, ModelSpec] = {
    'face_landmarker': ModelSpec(
        'face_landmarker', 'face_landmarker.task',
        'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task',
        '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff',
        'face detection, 478 landmarks, blendshapes (gaze/blink/mouth), head-pose matrix'),
    'object_detector': ModelSpec(
        'object_detector', 'efficientdet_lite0.tflite',
        'https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float16/latest/efficientdet_lite0.tflite',
        '4b59100025bea1235a84c1038879a6cccc9f6c49f5e41144e91e74d99e780993',
        'COCO object detection: phones, books, laptops, extra persons', required=False),
    'face_embedder': ModelSpec(
        'face_embedder', 'sface.onnx',
        'https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx',
        '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79',
        'face embedding for identity continuity (same person throughout)', required=False),
}


def model_path(model_dir: str | os.PathLike, key: str) -> Path:
    return Path(model_dir) / MODEL_SPECS[key].filename


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def verify(model_dir, key: str) -> bool:
    p = model_path(model_dir, key)
    return p.exists() and sha256_file(p) == MODEL_SPECS[key].sha256


def available(model_dir) -> dict[str, bool]:
    return {k: verify(model_dir, k) for k in MODEL_SPECS}


def fetch(model_dir, key: str, force: bool = False, timeout: int = 180) -> Path:
    spec = MODEL_SPECS[key]
    dest = model_path(model_dir, key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force and verify(model_dir, key):
        return dest
    tmp = dest.with_suffix(dest.suffix + '.part')
    req = urllib.request.Request(spec.url, headers={'User-Agent': 'proctoring-model-fetch/1'})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, 'wb') as f:  # noqa: S310 (pinned https URLs)
        while chunk := r.read(1 << 20):
            f.write(chunk)
    if sha256_file(tmp) != spec.sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f'Checksum mismatch for {spec.filename}; refusing to install')
    tmp.replace(dest)
    return dest
