"""
Per-session pipeline: extractor -> baseline -> derived features -> detectors.

* A failing detector never takes the session down; it is logged, counted and
  the others keep running.
* Per-detector latency is tracked so operators can see what costs what.
* The pipeline holds only *temporal* detector state in memory; anything that
  must survive a reconnect (baseline, identity template, ...) goes through
  ``export_state`` / ``import_state``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..vision import attention as attention_mod
from ..vision.baseline import Baseline
from .detector import Event, EventDetector, FeatureExtractor, FrameContext, FrameDetector, build_detectors
from .policy import Policy
from .types import Frame, Signal

logger = logging.getLogger(__name__)


@dataclass
class FrameResult:
    signals: list[Signal] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class Pipeline:
    def __init__(self, policy: Policy, extractor: FeatureExtractor | None = None,
                 state: Mapping[str, Any] | None = None, objects_every: int = 2):
        self.policy = policy
        self.extractor = extractor
        self.objects_every = max(1, objects_every)
        dets = build_detectors(policy.detectors, policy.detector_config)
        self.frame_detectors = [d for d in dets if isinstance(d, FrameDetector)]
        self.event_detectors = [d for d in dets if isinstance(d, EventDetector)]
        self.detectors = dets
        state = state or {}
        self.baseline = Baseline.from_dict(state.get('baseline'), duration_s=policy.calibration_s)
        for d in dets:
            s = (state.get('detectors') or {}).get(d.name)
            if s:
                d.import_state(s)
        self.stats: dict[str, dict[str, float]] = {}
        self._frame_count = 0
        self.last_summary: dict[str, Any] = {}

    # -- helpers --------------------------------------------------------------
    def detector(self, name: str):
        return next((d for d in self.detectors if d.name == name), None)

    def _record(self, name: str, dt: float, failed: bool = False):
        s = self.stats.setdefault(name, {'calls': 0, 'ms_total': 0.0, 'ms_max': 0.0, 'errors': 0})
        s['calls'] += 1
        s['ms_total'] += dt * 1000
        s['ms_max'] = max(s['ms_max'], dt * 1000)
        if failed:
            s['errors'] += 1

    def _gaze_cfg(self) -> dict:
        return {**self.policy.detector_config.get('gaze', {}), **self.policy.detector_config.get('head_pose', {})}

    # -- frames -----------------------------------------------------------------
    def process_frame(self, frame: Frame) -> FrameResult:
        if self.extractor is None:
            return FrameResult(error='no_extractor')
        self._frame_count += 1
        hints: dict[str, Any] = {'want_objects': self._frame_count % self.objects_every == 0}
        for d in self.frame_detectors:
            try:
                hints.update(d.hints(frame.ts))
            except Exception:  # pragma: no cover
                logger.exception('hints() failed in %s', d.name)
        frame.hints = hints

        t0 = time.perf_counter()
        try:
            features = self.extractor.extract(frame)
        except Exception as exc:
            logger.exception('feature extraction failed')
            self._record('extractor', time.perf_counter() - t0, True)
            return FrameResult(error=f'extract_failed:{type(exc).__name__}')
        self._record('extractor', time.perf_counter() - t0)
        return self.process_features(frame, features)

    def process_features(self, frame: Frame, features: dict[str, Any]) -> FrameResult:
        """Split out so tests (and replays) can inject synthetic features."""
        ts = frame.ts
        self.baseline.update(ts, features)
        primary = features.get('primary')
        if self.baseline.ready and primary is not None:
            features['attention'] = attention_mod.derive(primary, self.baseline, self._gaze_cfg())
        for d in self.event_detectors:
            d.on_activity(ts, 'frame', 'client')

        ctx = FrameContext(frame=frame, features=features, baseline=self.baseline.values)
        signals: list[Signal] = []
        for d in self.frame_detectors:
            if any(features.get(k) is None for k in d.requires):
                continue
            t0 = time.perf_counter()
            try:
                signals.extend(d.process(ctx))
                self._record(d.name, time.perf_counter() - t0)
            except Exception:
                logger.exception('detector %s failed', d.name)
                self._record(d.name, time.perf_counter() - t0, True)

        meta = features.get('meta', {})
        self.last_summary = {
            'faces': features.get('n_faces', 0),
            'calibrated': self.baseline.ready,
            'brightness': round(meta.get('brightness', 0), 1),
            'sharpness': round(meta.get('sharpness', 0), 1),
            'objects': [o.label for o in features.get('objects', [])] if features.get('objects_ran') else None,
        }
        return FrameResult(signals=signals, summary=dict(self.last_summary))

    # -- events -----------------------------------------------------------------
    def process_event(self, event: Event) -> list[Signal]:
        for d in self.event_detectors:
            d.on_activity(event.ts, event.kind, event.origin)
        signals: list[Signal] = []
        for d in self.event_detectors:
            if d.handles and event.kind not in d.handles:
                continue
            t0 = time.perf_counter()
            try:
                signals.extend(d.process(event))
                self._record(d.name, time.perf_counter() - t0)
            except Exception:
                logger.exception('event detector %s failed on %s', d.name, event.kind)
                self._record(d.name, time.perf_counter() - t0, True)
        return signals

    def tick(self, now: float) -> list[Signal]:
        out: list[Signal] = []
        for d in self.event_detectors:
            try:
                out.extend(d.tick(now))
            except Exception:
                logger.exception('tick failed in %s', d.name)
        return out

    # -- persistence -------------------------------------------------------------
    def export_state(self) -> dict:
        return {'baseline': self.baseline.to_dict(),
                'detectors': {d.name: s for d in self.detectors if (s := d.export_state())}}

    def reset_temporal(self):
        for d in self.detectors:
            d.reset()
