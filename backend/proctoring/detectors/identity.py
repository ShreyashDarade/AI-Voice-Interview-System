"""
Identity continuity: is it the same person at minute 40 as at minute 1?
(and, optionally, the person in the reference photo supplied by the integrator).

Uses an on-device face-embedding model (OpenCV SFace). Embeddings are session
scoped: only the averaged template is kept (as numbers, not an image) and it is
deleted with the session -- see docs/COMPLIANCE.md (biometric handling).
"""
from __future__ import annotations

from collections import deque

import numpy as np

from ..framework.detector import FrameContext, FrameDetector, register
from ..framework.types import Category, Severity, Signal, Source
from ..framework.util import clamp01


def cosine(a, b) -> float:
    a, b = np.asarray(a, np.float32), np.asarray(b, np.float32)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


@register
class IdentityDetector(FrameDetector):
    name = 'identity'
    emits = ('identity_changed', 'identity_mismatch')
    requires = frozenset({'embedding'})

    ENROLL_N = 5

    def __init__(self, config=None):
        super().__init__(config)
        self.embed_every_s = self.config.get('embed_every_s', 4.0)
        self.change_thr = self.config.get('change_threshold', 0.28)     # SFace cosine; same person is typically >0.45
        self.reference_thr = self.config.get('reference_threshold', 0.30)
        self.max_yaw = self.config.get('max_yaw_deg', 20.0)
        self.confirm = self.config.get('confirm_count', 3)
        self._enroll: list[np.ndarray] = []
        self.template: np.ndarray | None = None
        self.reference: np.ndarray | None = None
        self._recent: deque = deque(maxlen=6)
        self._low = 0
        self._last_embed_ts = -1e9
        self._mismatch_sent = False
        self.last_similarity: float | None = None

    # -- extractor hint: only compute embeddings at our cadence ---------------
    def hints(self, ts: float) -> dict:
        interval = 1.0 if self.template is None else self.embed_every_s
        return {'want_embedding': (ts - self._last_embed_ts) >= interval}

    def set_reference(self, embedding) -> None:
        self.reference = None if embedding is None else np.asarray(embedding, np.float32)

    def reset(self):
        self._recent.clear()
        self._low = 0

    def export_state(self):
        if self.template is None:
            return None
        return {'template': self.template.tolist(), 'mismatch_sent': self._mismatch_sent}

    def import_state(self, state):
        if state and state.get('template'):
            self.template = np.asarray(state['template'], np.float32)
            self._mismatch_sent = bool(state.get('mismatch_sent'))

    # -- main -------------------------------------------------------------------
    def process(self, ctx: FrameContext):
        emb = ctx.get('embedding')
        ts = ctx.frame.ts
        if emb is None:
            return []
        face = ctx.get('primary')
        a = ctx.get('attention')
        # only trust near-frontal, eyes-open, single-face crops
        if ctx.get('n_faces') != 1 or face is None or face.blink > 0.6:
            return []
        yaw = a['yaw_dev'] if a else face.yaw
        if abs(yaw) > self.max_yaw:
            return []
        self._last_embed_ts = ts
        out = []

        if self.template is None:
            self._enroll.append(emb)
            if len(self._enroll) >= self.ENROLL_N:
                sims = [cosine(self._enroll[i], self._enroll[j])
                        for i in range(len(self._enroll)) for j in range(i + 1, len(self._enroll))]
                if float(np.mean(sims)) < 0.5:      # inconsistent enrolment (two people?) -> start over
                    self._enroll = self._enroll[-2:]
                    return []
                t = np.mean(self._enroll, axis=0)
                self.template = t / (np.linalg.norm(t) + 1e-9)
                self._enroll.clear()
                if self.reference is not None and not self._mismatch_sent:
                    s = cosine(self.template, self.reference)
                    self.last_similarity = s
                    if s < self.reference_thr:
                        self._mismatch_sent = True
                        out.append(Signal(kind='identity_mismatch', category=Category.IDENTITY,
                                          severity=Severity.CRITICAL,
                                          confidence=clamp01(0.7 + (self.reference_thr - s)), source=Source.VISION,
                                          details={'similarity': round(s, 3), 'threshold': self.reference_thr},
                                          ts=ts, capture_evidence=True))
            return out

        s = cosine(emb, self.template)
        self.last_similarity = s
        self._recent.append(s)
        self._low = self._low + 1 if s < self.change_thr else 0
        if self._low >= self.confirm:
            med = float(np.median(list(self._recent)[-self.confirm:]))
            out.append(Signal(kind='identity_changed', category=Category.IDENTITY, severity=Severity.CRITICAL,
                              confidence=clamp01(0.7 + (self.change_thr - med) * 1.5), source=Source.VISION,
                              details={'similarity': round(med, 3), 'threshold': self.change_thr,
                                       'consecutive': self._low}, ts=ts, capture_evidence=True))
        return out
