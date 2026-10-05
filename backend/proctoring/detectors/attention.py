"""Attention: gaze, head pose, reading-from-elsewhere patterns, periodic glances."""
from __future__ import annotations

from collections import deque

import numpy as np

from ..framework.detector import FrameContext, FrameDetector, register
from ..framework.types import Category, Severity, Signal, Source
from ..framework.util import Sustained, clamp01


def _sig(kind, sev, conf, ts, details=None, evidence=False):
    return Signal(kind=kind, category=Category.ATTENTION, severity=sev, confidence=clamp01(conf),
                  source=Source.VISION, details=details or {}, ts=ts, capture_evidence=evidence)


@register
class GazeDetector(FrameDetector):
    """Sustained gaze away from the screen, relative to the candidate's own baseline."""
    name = 'gaze'
    emits = ('gaze_off_screen', 'looking_down')
    requires = frozenset({'attention'})

    def __init__(self, config=None):
        super().__init__(config)
        self.min_duration_s = self.config.get('min_duration_s', 3.5)
        self._away = Sustained(off_grace_s=0.8)
        self._down = Sustained(off_grace_s=0.8)

    def reset(self):
        self._away.reset()
        self._down.reset()

    def process(self, ctx: FrameContext):
        a, ts, out = ctx.get('attention'), ctx.frame.ts, []
        off = a['gaze_score'] >= 1.0
        # "down" = vertical component dominates (notes / phone in lap / keyboard)
        is_down = off and a['eff_pitch'] < 0 and abs(a['eff_yaw']) < 0.6 * 25.0
        d_down = self._down.update(is_down, ts)
        d_away = self._away.update(off and not is_down, ts)
        if d_away >= self.min_duration_s:
            conf = 0.5 + 0.2 * min(2.0, a['gaze_score'] - 1.0) + 0.3 * min(1.0, (d_away - self.min_duration_s) / 8)
            out.append(_sig('gaze_off_screen', Severity.LOW, conf, ts,
                            {'duration_s': round(d_away, 1), 'eff_yaw': round(a['eff_yaw'], 1),
                             'eff_pitch': round(a['eff_pitch'], 1)}))
        if d_down >= self.min_duration_s * 1.5:
            conf = 0.5 + 0.5 * min(1.0, (d_down - self.min_duration_s * 1.5) / 8 + 0.2)
            out.append(_sig('looking_down', Severity.LOW, conf, ts,
                            {'duration_s': round(d_down, 1), 'eff_pitch': round(a['eff_pitch'], 1)}))
        return out


@register
class HeadPoseDetector(FrameDetector):
    name = 'head_pose'
    emits = ('head_turned_away',)
    requires = frozenset({'attention'})

    def __init__(self, config=None):
        super().__init__(config)
        self.min_duration_s = self.config.get('min_duration_s', 3.5)
        self.yaw_limit = self.config.get('yaw_limit_deg', 28.0)
        self._turn = Sustained(off_grace_s=0.8)

    def reset(self):
        self._turn.reset()

    def process(self, ctx: FrameContext):
        a, ts = ctx.get('attention'), ctx.frame.ts
        d = self._turn.update(abs(a['yaw_dev']) >= self.yaw_limit, ts)
        if d >= self.min_duration_s:
            conf = 0.55 + 0.2 * min(1.0, abs(a['yaw_dev']) / (2 * self.yaw_limit)) + 0.25 * min(1.0, (d - self.min_duration_s) / 8)
            return [_sig('head_turned_away', Severity.LOW, conf, ts,
                         {'duration_s': round(d, 1), 'yaw_dev': round(a['yaw_dev'], 1)})]
        return []


@register
class ReadingPatternDetector(FrameDetector):
    """
    Experimental. Reading text on a second screen produces a *sawtooth* in the
    horizontal eye signal: a slow sweep along a line followed by a quick return.
    Natural conversation gaze is irregular and (when talking to a camera) mostly
    centred. We count sweep/return cycles with a still head. Low weight by
    default; validate against your own data before raising it.
    """
    name = 'reading_pattern'
    emits = ('reading_pattern',)
    requires = frozenset({'attention'})

    def __init__(self, config=None):
        super().__init__(config)
        self.window_s = self.config.get('window_s', 30.0)
        self.min_cycles = self.config.get('min_cycles', 4)
        self.min_rise = self.config.get('min_rise', 0.18)
        self._buf: deque = deque()
        self._last_eval = 0.0

    def reset(self):
        self._buf.clear()

    def process(self, ctx: FrameContext):
        a, ts = ctx.get('attention'), ctx.frame.ts
        self._buf.append((ts, a['eye_h_dev'], a['yaw_dev']))
        while self._buf and ts - self._buf[0][0] > self.window_s:
            self._buf.popleft()
        if ts - self._last_eval < 2.0 or len(self._buf) < 24:
            return []
        self._last_eval = ts
        t = np.array([b[0] for b in self._buf])
        x = np.array([b[1] for b in self._buf])
        yaw = np.array([b[2] for b in self._buf])
        if float(yaw.std()) > 7.0:        # head moving a lot -> not a still reader
            return []
        cycles = self.count_sweeps(t, x, self.min_rise)
        if len(cycles) < self.min_cycles:
            return []
        periods = np.diff([c for c in cycles])
        cv = float(periods.std() / periods.mean()) if len(periods) >= 2 and periods.mean() > 0 else 1.0
        if cv > 0.5:
            return []
        conf = 0.55 + 0.08 * (len(cycles) - self.min_cycles) + 0.2 * (0.5 - cv)
        return [_sig('reading_pattern', Severity.MEDIUM, conf, ts,
                     {'cycles': len(cycles), 'period_cv': round(cv, 2),
                      'mean_period_s': round(float(periods.mean()), 1)}, evidence=True)]

    @staticmethod
    def count_sweeps(t: np.ndarray, x: np.ndarray, min_rise: float) -> list[float]:
        """Return the timestamps of 'return saccades' that end a sweep."""
        ends: list[float] = []
        i, n = 0, len(x)
        while i < n - 1:
            # grow a monotone-ish rise from a local minimum
            lo = i
            j = i + 1
            peak = j
            while j < n and x[j] >= x[peak] - 0.04:
                if x[j] > x[peak]:
                    peak = j
                j += 1
            rise = x[peak] - x[lo]
            if rise >= min_rise and peak - lo >= 3 and peak + 1 < n:
                # a return = big drop within the next 2 samples
                drop = x[peak] - x[peak + 1: peak + 3].min()
                if drop >= 0.6 * rise:
                    ends.append(float(t[peak]))
                    i = peak + 1
                    continue
            i = max(i + 1, peak if peak > i else i + 1)
        return ends


@register
class PeriodicGlanceDetector(FrameDetector):
    """Regular or very frequent short glances away -> consulting something off-screen."""
    name = 'periodic_glance'
    emits = ('periodic_glance',)
    requires = frozenset({'attention'})

    def __init__(self, config=None):
        super().__init__(config)
        self.window_s = self.config.get('window_s', 300.0)
        self.min_glance_s = self.config.get('min_glance_s', 1.0)
        self.max_glance_s = self.config.get('max_glance_s', 8.0)
        self._cur = Sustained(off_grace_s=0.6)
        self._in_glance = False
        self._glance_start = 0.0
        self.episodes: list[tuple[float, float]] = []      # (start, duration)

    def reset(self):
        self._cur.reset()
        self._in_glance = False
        self.episodes.clear()

    def process(self, ctx: FrameContext):
        a, ts = ctx.get('attention'), ctx.frame.ts
        d = self._cur.update(a['gaze_score'] >= 1.0, ts)
        if d > 0 and not self._in_glance:
            self._in_glance, self._glance_start = True, ts
        elif d == 0 and self._in_glance:
            self._in_glance = False
            dur = ts - self._glance_start
            if self.min_glance_s <= dur <= self.max_glance_s:
                self.episodes.append((self._glance_start, dur))
        self.episodes = [e for e in self.episodes if ts - e[0] <= self.window_s]
        return self._evaluate(ts)

    def _evaluate(self, ts: float):
        if len(self.episodes) < 5:
            return []
        starts = np.array([e[0] for e in self.episodes])
        gaps = np.diff(starts)
        gaps = gaps[gaps > 0]
        if len(gaps) < 4:
            return []
        cv = float(gaps.std() / gaps.mean())
        rate_per_min = len(self.episodes) / max(1.0, (starts[-1] - starts[0]) / 60.0)
        if cv < 0.2 and gaps.mean() > 3.0:
            return [_sig('periodic_glance', Severity.MEDIUM, 0.65 + 0.2 * (0.2 - cv) / 0.2, ts,
                         {'episodes': len(self.episodes), 'gap_cv': round(cv, 2),
                          'mean_gap_s': round(float(gaps.mean()), 1)}, evidence=True)]
        if rate_per_min >= 6 and len(self.episodes) >= 8:
            return [_sig('periodic_glance', Severity.LOW, 0.6, ts,
                         {'episodes': len(self.episodes), 'per_minute': round(rate_per_min, 1)})]
        return []
