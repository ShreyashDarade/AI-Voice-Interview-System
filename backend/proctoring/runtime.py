"""
In-process runtime for a live session: the detector pipeline (temporal state)
plus the shared, lazily created vision extractor.

Only *temporal* detector state lives here. Anything that decides a verdict is
persisted via services.apply_signals, so losing a runtime (restart, other
worker) can never lose or corrupt evidence -- it only costs a few seconds of
detector warm-up.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from .framework.detector import Event
from .framework.pipeline import FrameResult, Pipeline
from .framework.types import Frame, Signal
from .models import ProctorSession
from .services import policy_for
from .vision.imaging import BadFrame, decode_jpeg

logger = logging.getLogger(__name__)

_extractor = None
_extractor_lock = threading.Lock()
_extractor_failed = False


def get_extractor():
    """Process-wide VisionExtractor, or None if models are unavailable (degraded mode)."""
    global _extractor, _extractor_failed
    if _extractor is not None or _extractor_failed:
        return _extractor
    with _extractor_lock:
        if _extractor is None and not _extractor_failed:
            cfg = settings.PROCTOR
            try:
                from .vision.engine import VisionExtractor
                ex = VisionExtractor(cfg['MODEL_DIR'], pool_size=cfg['POOL_SIZE'],
                                     use_objects=cfg['ENABLE_OBJECTS'], use_embedding=cfg['ENABLE_IDENTITY'])
                if not ex.capabilities()['faces']:
                    raise FileNotFoundError('face_landmarker model missing - run `manage.py fetch_proctor_models`')
                _extractor = ex
            except Exception as exc:
                _extractor_failed = True
                logger.error('Vision disabled: %s', exc)
    return _extractor


def reset_extractor_for_tests():
    global _extractor, _extractor_failed
    if _extractor is not None:
        _extractor.close()
    _extractor, _extractor_failed = None, False


def capabilities() -> dict:
    ex = get_extractor()
    return {'video': ex is not None, **(ex.capabilities() if ex else
                                         {'faces': False, 'objects': False, 'identity': False})}


@dataclass
class SessionRuntime:
    session_id: str
    pipeline: Pipeline
    created: float = field(default_factory=time.time)
    frames: int = 0
    dropped: int = 0
    last_jpeg: bytes | None = None
    last_state_save: float = field(default_factory=time.time)
    _frame_times: list = field(default_factory=list)

    # -- frames (CPU heavy: call from a worker thread) --------------------------
    def handle_frame(self, jpeg: bytes, *, seq: int = 0, client_ts: float | None = None,
                     arrival: float | None = None) -> FrameResult:
        arrival = arrival if arrival is not None else time.time()
        try:
            img = decode_jpeg(jpeg)
        except BadFrame as exc:
            return FrameResult(error=f'bad_frame:{exc}')
        self.frames += 1
        self.last_jpeg = jpeg
        frame = Frame(image=img, ts=arrival, seq=seq, client_ts=client_ts, jpeg_size=len(jpeg))
        return self.pipeline.process_frame(frame)

    def handle_event(self, kind: str, data: dict, *, origin: str = 'client', ts: float | None = None) -> list[Signal]:
        return self.pipeline.process_event(Event(kind=kind, ts=ts if ts is not None else time.time(),
                                                 data=data, origin=origin))

    def tick(self, now: float | None = None) -> list[Signal]:
        return self.pipeline.tick(now if now is not None else time.time())

    def allow_frame(self, now: float, max_hz: float) -> bool:
        """Token-bucket-ish guard: refuse floods well above the configured rate."""
        self._frame_times = [t for t in self._frame_times if now - t < 2.0]
        if len(self._frame_times) >= max_hz * 2.0:
            return False
        self._frame_times.append(now)
        return True


def build_runtime(s: ProctorSession) -> SessionRuntime:
    policy = policy_for(s)
    state: dict[str, Any] = {'baseline': s.baseline, 'detectors': s.detector_state}
    pipe = Pipeline(policy, get_extractor(), state=state)
    ident = pipe.detector('identity')
    if ident is not None and s.reference_embedding:
        ident.set_reference(s.reference_embedding)
    return SessionRuntime(session_id=str(s.id), pipeline=pipe)
