"""Pre-interview camera check: tells the candidate what to fix *before* the clock starts."""
from __future__ import annotations

from typing import Any, Mapping

# issue code -> candidate-facing instruction
INSTRUCTIONS = {
    'no_face': 'We cannot see your face. Move into view of the camera.',
    'multiple_faces': 'More than one person is visible. Please be alone in the frame.',
    'face_too_small': 'You are too far from the camera. Move closer.',
    'face_too_large': 'You are too close to the camera. Move back a little.',
    'off_center': 'Please centre your face in the frame.',
    'too_dark': 'The image is too dark. Add light in front of you.',
    'too_bright': 'The image is overexposed. Reduce light behind or on you.',
    'blurry': 'The image is blurry. Clean the lens or improve the lighting.',
    'low_resolution': 'The camera resolution is too low. Use a different camera.',
    'not_frontal': 'Please face the screen directly.',
    'eyes_closed': 'Please keep your eyes open and look at the screen.',
    'phone_visible': 'A phone is visible. Please move it out of view.',
    'camera_unavailable': 'Video analysis is not available; please contact support.',
}


def assess_readiness(features: Mapping[str, Any]) -> dict:
    issues: list[str] = []
    meta = features.get('meta') or {}
    n = features.get('n_faces', 0)
    p = features.get('primary')
    if meta.get('width', 0) < 320:
        issues.append('low_resolution')
    if meta.get('brightness', 128) < 45:
        issues.append('too_dark')
    elif meta.get('brightness', 128) > 230:
        issues.append('too_bright')
    if meta.get('sharpness', 100) < 15:
        issues.append('blurry')
    if n == 0:
        issues.append('no_face')
    elif n > 1 and features['faces'][1].area / max(features['faces'][0].area, 1e-6) >= 0.12:
        issues.append('multiple_faces')
    if p is not None:
        if p.area < 0.03:
            issues.append('face_too_small')
        elif p.area > 0.55:
            issues.append('face_too_large')
        cx, cy = p.center
        if abs(cx - 0.5) > 0.22 or abs(cy - 0.5) > 0.25:
            issues.append('off_center')
        if abs(p.yaw) > 25 or abs(p.pitch) > 25:
            issues.append('not_frontal')
        if p.blink > 0.6:
            issues.append('eyes_closed')
    if any(o.label == 'cell phone' and o.score >= 0.5 for o in features.get('objects', [])):
        issues.append('phone_visible')
    return {'ready': not issues, 'issues': issues,
            'instructions': [INSTRUCTIONS[i] for i in issues],
            'metrics': {'faces': n, 'brightness': round(meta.get('brightness', 0), 1),
                        'sharpness': round(meta.get('sharpness', 0), 1),
                        'face_area': round(p.area, 3) if p else 0.0}}
