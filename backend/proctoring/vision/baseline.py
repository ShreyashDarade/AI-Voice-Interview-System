"""
Per-candidate calibration.

Webcams are rarely centred on the screen, people sit at different angles, and
some candidates' resting gaze is off-axis. Rather than applying universal angle
limits (a documented source of false positives in commercial proctors), we
learn each candidate's *own* neutral pose during the first seconds of a clean
session and measure deviation from it.
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from ..framework.util import mad

KEYS = ('yaw', 'pitch', 'gaze_h', 'gaze_v', 'area')


class Baseline:
    def __init__(self, duration_s: float = 8.0, min_samples: int = 6, max_pose: float = 35.0):
        self.duration_s = duration_s
        self.min_samples = min_samples
        self.max_pose = max_pose
        self._samples: dict[str, list[float]] = {k: [] for k in KEYS}
        self._start: float | None = None
        self._last: float | None = None
        self.values: dict[str, float] = {}
        self.spread: dict[str, float] = {}
        self.ready = False

    # -- accumulation ---------------------------------------------------------
    def update(self, ts: float, features: Mapping[str, Any]) -> bool:
        if self.ready:
            return True
        face = features.get('primary')
        if features.get('n_faces') != 1 or face is None:
            return False
        if abs(face.yaw) > self.max_pose or abs(face.pitch) > self.max_pose or face.blink > 0.6:
            return False
        if self._last is not None and ts - self._last > 5.0:      # gap -> start over
            self._reset_samples()
        if self._start is None:
            self._start = ts
        self._last = ts
        for k in KEYS:
            self._samples[k].append(float(getattr(face, k)))
        if ts - self._start >= self.duration_s and len(self._samples['yaw']) >= self.min_samples:
            self._freeze()
        return self.ready

    def _reset_samples(self):
        self._samples = {k: [] for k in KEYS}
        self._start = self._last = None

    def _freeze(self):
        self.values = {k: float(np.median(v)) for k, v in self._samples.items()}
        self.spread = {k: mad(v) for k, v in self._samples.items()}
        self.ready = True

    # -- persistence ----------------------------------------------------------
    def to_dict(self) -> dict:
        return {'ready': self.ready, 'values': self.values, 'spread': self.spread}

    @classmethod
    def from_dict(cls, d: Mapping | None, **kw) -> 'Baseline':
        b = cls(**kw)
        if d and d.get('ready'):
            b.values, b.spread, b.ready = dict(d['values']), dict(d.get('spread', {})), True
        return b

    def get(self, key: str, default: float = 0.0) -> float:
        return self.values.get(key, default)
