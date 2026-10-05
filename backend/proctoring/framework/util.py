"""Small stateful helpers shared by detectors."""
from __future__ import annotations

from collections import deque
from typing import Deque

import numpy as np


class Sustained:
    """Tracks how long a boolean condition has been continuously true.

    * robust to dropped frames: a gap larger than ``max_gap_s`` between updates
      resets the state (we do not know what happened in the gap);
    * ``off_grace_s`` lets a condition flicker off briefly (e.g. a blink while
      looking away) without restarting the clock.
    """

    def __init__(self, max_gap_s: float = 3.0, off_grace_s: float = 0.0):
        self.max_gap_s = max_gap_s
        self.off_grace_s = off_grace_s
        self._start: float | None = None
        self._last_ts: float | None = None
        self._last_true: float | None = None

    def update(self, active: bool, ts: float) -> float:
        if self._last_ts is not None and ts - self._last_ts > self.max_gap_s:
            self._start = self._last_true = None
        self._last_ts = ts
        if active:
            if self._start is None:
                self._start = ts
            self._last_true = ts
            return ts - self._start
        if self._start is not None and self._last_true is not None and ts - self._last_true <= self.off_grace_s:
            return ts - self._start
        self._start = self._last_true = None
        return 0.0

    @property
    def active(self) -> bool:
        return self._start is not None

    def reset(self):
        self._start = self._last_ts = self._last_true = None


class SlidingHits:
    """'at least k of the last n observations were positive'."""

    def __init__(self, window: int, min_hits: int):
        self.buf: Deque[bool] = deque(maxlen=window)
        self.min_hits = min_hits

    def push(self, hit: bool) -> bool:
        self.buf.append(bool(hit))
        return sum(self.buf) >= self.min_hits and len(self.buf) >= min(self.min_hits, self.buf.maxlen)

    @property
    def count(self) -> int:
        return sum(self.buf)

    def clear(self):
        self.buf.clear()


def mad(values) -> float:
    a = np.asarray(values, dtype=np.float64)
    if a.size == 0:
        return 0.0
    return float(np.median(np.abs(a - np.median(a))))


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else float(x)
