"""
Video feed integrity: catches frozen cameras, a photo held up to the lens,
and pre-recorded loops fed through a virtual camera -- using only pixel and
landmark statistics (no deepfake classifier; see docs for the honest limits).
"""
from __future__ import annotations

from collections import deque

import numpy as np

from ..framework.detector import FrameContext, FrameDetector, register
from ..framework.types import Category, Severity, Signal, Source
from ..framework.util import clamp01


def _sig(kind, sev, conf, ts, details, evidence=True):
    return Signal(kind=kind, category=Category.TAMPERING, severity=sev, confidence=clamp01(conf),
                  source=Source.VISION, details=details, ts=ts, capture_evidence=evidence)


@register
class FeedIntegrityDetector(FrameDetector):
    name = 'feed_integrity'
    emits = ('frozen_feed', 'static_image_suspected', 'feed_loop_suspected', 'frame_timing_anomaly')
    requires = frozenset({'thumb'})

    EXACT_DIFF = 0.02        # mean-abs-diff of 24x24 thumbs; live sensors never repeat this exactly

    def __init__(self, config=None):
        super().__init__(config)
        self.frozen_s = self.config.get('frozen_s', 6.0)
        self.static_window_s = self.config.get('static_window_s', 45.0)
        self.loop_min_lag_s = self.config.get('loop_min_lag_s', 8.0)
        self.loop_max_lag_s = self.config.get('loop_max_lag_s', 120.0)
        self._thumbs: deque = deque(maxlen=400)           # (ts, thumb)
        self._exact: deque = deque(maxlen=64)             # (ts, was_exact_duplicate)
        self._pose: deque = deque(maxlen=200)             # (ts, yaw, pitch, gaze_h, gaze_v, blink, mouth)
        self._last_client_ts: float | None = None
        self._last_loop_eval = 0.0

    def reset(self):
        self._thumbs.clear()
        self._exact.clear()
        self._pose.clear()
        self._last_client_ts = None

    def process(self, ctx: FrameContext):
        ts, th = ctx.frame.ts, ctx.get('thumb')
        out = []

        # ---- timing -----------------------------------------------------------
        cts = ctx.frame.client_ts
        if cts is not None:
            if self._last_client_ts is not None and cts <= self._last_client_ts:
                out.append(_sig('frame_timing_anomaly', Severity.MEDIUM, 0.7, ts,
                                {'reason': 'non_monotonic_client_ts'}, evidence=False))
            self._last_client_ts = cts

        # ---- frozen / exact duplicate frames -----------------------------------
        if self._thumbs:
            d = float(np.abs(th - self._thumbs[-1][1]).mean())
            self._exact.append((ts, d < self.EXACT_DIFF))
        self._thumbs.append((ts, th))
        recent = [e for e in self._exact if ts - e[0] <= self.frozen_s + 1.0]
        if recent and ts - recent[0][0] >= self.frozen_s and np.mean([e[1] for e in recent]) >= 0.9:
            span = ts - recent[0][0]
            out.append(_sig('frozen_feed', Severity.HIGH, 0.8 + 0.15 * min(1.0, span / 20.0), ts,
                            {'span_s': round(span, 1)}))

        # ---- static image: a face that never blinks, never moves ----------------
        p = ctx.get('primary')
        if p is not None and ctx.get('n_faces') == 1:
            self._pose.append((ts, p.yaw, p.pitch, p.gaze_h, p.gaze_v, p.blink, p.mouth))
        arr = np.array([r for r in self._pose if ts - r[0] <= self.static_window_s])
        if len(arr) >= 40 and arr[-1, 0] - arr[0, 0] >= self.static_window_s * 0.9:
            # thresholds sit above webcam sensor-noise jitter measured on a perfectly
            # still photo (yaw 0.07 deg, pitch 0.22 deg, gaze 0.02) and below typical
            # live-subject variation. Treat as experimental: calibrate on your data.
            c = self.config
            no_blink = float(arr[:, 5].max()) < c.get('max_blink', 0.4)
            pose_still = (float(arr[:, 1].std()) < c.get('pose_std_deg', 0.4)
                          and float(arr[:, 2].std()) < c.get('pose_std_deg', 0.4))
            gaze_still = (float(arr[:, 3].std()) < c.get('gaze_std', 0.03)
                          and float(arr[:, 4].std()) < c.get('gaze_std', 0.03))
            mouth_still = float(arr[:, 6].std()) < c.get('mouth_std', 0.015)
            if no_blink and pose_still and gaze_still and mouth_still:
                out.append(_sig('static_image_suspected', Severity.HIGH, 0.75, ts,
                                {'window_s': self.static_window_s, 'yaw_std': round(float(arr[:, 1].std()), 3),
                                 'max_blink': round(float(arr[:, 5].max()), 2)}))

        # ---- loop: current seconds are a near-exact repeat of an earlier span ----
        if ts - self._last_loop_eval >= 5.0 and len(self._thumbs) > 40:
            self._last_loop_eval = ts
            loop = self._detect_loop(ts)
            if loop:
                out.append(_sig('feed_loop_suspected', Severity.CRITICAL, 0.85, ts, loop))
        return out

    def _detect_loop(self, ts: float):
        T = [(t, x) for t, x in self._thumbs]
        times = np.array([t for t, _ in T])
        X = np.stack([x for _, x in T])
        # live scene activity: adjacent frame differences over the last ~20 frames
        n_adj = min(20, len(X) - 1)
        adj = np.abs(np.diff(X[-n_adj - 1:], axis=0)).mean(axis=(1, 2))
        if adj.size < 8 or float(adj.mean()) < 0.5:       # static scene: cannot judge loops
            return None
        probe = X[-4:]
        best = None
        for lag_s in np.arange(self.loop_min_lag_s, self.loop_max_lag_s, 0.5):
            idx = np.searchsorted(times, times[-4:] - lag_s)
            if idx[0] <= 0 or (times[-4:] - lag_s)[0] < times[0]:
                continue
            if np.any(np.abs(times[idx.clip(max=len(times) - 1)] - (times[-4:] - lag_s)) > 0.6):
                continue
            ref = X[idx.clip(max=len(X) - 1)]
            d = float(np.abs(probe - ref).mean())
            if best is None or d < best[0]:
                best = (d, float(lag_s))
        if best and best[0] < 0.05 and best[0] < 0.1 * float(adj.mean()):
            return {'lag_s': best[1], 'distance': round(best[0], 4), 'live_motion': round(float(adj.mean()), 3)}
        return None
