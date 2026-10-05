"""Safe, bounded image decoding + cheap image statistics."""
from __future__ import annotations

import struct

import numpy as np

MAX_JPEG_BYTES = 600_000
MAX_DIM = 4096
TARGET_WIDTH = 640


class BadFrame(ValueError):
    pass


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Read (w, h) from JPEG SOF without decoding -> decompression-bomb guard."""
    if data[:3] != b'\xff\xd8\xff':
        raise BadFrame('not a JPEG')
    i, n = 2, len(data)
    while i + 4 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xFF:
            i += 1
            continue
        (seg_len,) = struct.unpack('>H', data[i + 2:i + 4])
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack('>HH', data[i + 5:i + 9])
            return w, h
        i += 2 + seg_len
    raise BadFrame('no SOF marker')


def decode_jpeg(data: bytes, target_width: int = TARGET_WIDTH) -> np.ndarray:
    import cv2
    if len(data) > MAX_JPEG_BYTES:
        raise BadFrame('frame too large')
    w, h = jpeg_dimensions(data)
    if w < 64 or h < 64 or w > MAX_DIM or h > MAX_DIM:
        raise BadFrame(f'unsupported frame size {w}x{h}')
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise BadFrame('corrupt JPEG')
    if img.shape[1] > target_width:
        scale = target_width / img.shape[1]
        img = cv2.resize(img, (target_width, max(1, int(img.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    return img


def frame_stats(img: np.ndarray) -> dict:
    import cv2
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (160, int(160 * gray.shape[0] / gray.shape[1])), interpolation=cv2.INTER_AREA)
    lap = cv2.Laplacian(small, cv2.CV_64F)
    return {
        'width': int(img.shape[1]), 'height': int(img.shape[0]),
        'brightness': float(gray.mean()), 'contrast': float(gray.std()),
        'sharpness': float(lap.var()),
    }


def thumbnail(img: np.ndarray, size: int = 24) -> np.ndarray:
    import cv2
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
