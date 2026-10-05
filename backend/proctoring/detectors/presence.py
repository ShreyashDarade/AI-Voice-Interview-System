"""Who/what is in front of the camera: presence, extra faces, framing, image quality."""
from __future__ import annotations

from collections import deque

import numpy as np

from ..framework.detector import FrameContext, FrameDetector, register
from ..framework.types import Category, Severity, Signal, Source
from ..framework.util import Sustained, clamp01


def _sig(kind, cat, sev, conf, ts, details=None, evidence=False):
    return Signal(kind=kind, category=cat, severity=sev, confidence=clamp01(conf), source=Source.VISION,
                  details=details or {}, ts=ts, capture_evidence=evidence)


@register
class FacePresenceDetector(FrameDetector):
    name = 'face_presence'
    emits = ('face_missing', 'multiple_faces', 'face_out_of_frame')
    requires = frozenset({'n_faces'})

    def __init__(self, config=None):
        super().__init__(config)
        self.min_missing_s = self.config.get('min_missing_s', 4.0)
        self.min_multi_s = self.config.get('min_multi_s', 2.0)
        self.min_secondary_ratio = self.config.get('min_secondary_ratio', 0.12)
        self.min_framing_s = self.config.get('min_framing_s', 6.0)
        self._missing = Sustained(off_grace_s=1.0)
        self._multi = Sustained(off_grace_s=1.0)
        self._framing = Sustained(off_grace_s=1.0)
        self._second_pos: deque = deque(maxlen=24)

    def reset(self):
        for s in (self._missing, self._multi, self._framing):
            s.reset()
        self._second_pos.clear()

    def process(self, ctx: FrameContext):
        ts = ctx.frame.ts
        n = ctx.get('n_faces', 0)
        faces = ctx.get('faces', [])
        meta = ctx.get('meta', {})
        out = []

        # -- no face (unless the camera is simply covered: video_quality owns that) --
        covered = meta.get('brightness', 255) < 15 or meta.get('contrast', 99) < 6
        miss = self._missing.update(n == 0 and not covered, ts)
        if miss >= self.min_missing_s:
            conf = 0.6 + 0.35 * min(1.0, (miss - self.min_missing_s) / 10.0)
            out.append(_sig('face_missing', Category.PRESENCE, Severity.MEDIUM, conf, ts,
                            {'duration_s': round(miss, 1)}, evidence=True))

        # -- more than one face --
        multi_active, ratio = False, 0.0
        if n >= 2:
            ratio = faces[1].area / max(faces[0].area, 1e-6)
            multi_active = ratio >= self.min_secondary_ratio
            if multi_active:
                self._second_pos.append(faces[1].center)
        else:
            self._second_pos.clear()
        dur = self._multi.update(multi_active, ts)
        if dur >= self.min_multi_s:
            conf = 0.7 + 0.25 * min(1.0, ratio)
            # a face on a poster/photo never moves: soften confidence
            if len(self._second_pos) >= 12:
                pos = np.array(self._second_pos)
                if float(pos.std(axis=0).max()) < 0.003:
                    conf *= 0.6
            out.append(_sig('multiple_faces', Category.PRESENCE, Severity.HIGH, conf, ts,
                            {'faces': n, 'secondary_ratio': round(ratio, 2), 'duration_s': round(dur, 1)},
                            evidence=True))

        # -- framing: face cut off by the frame edge or tiny --
        bad_frame = False
        if n >= 1:
            x0, y0, x1, y1 = faces[0].bbox
            bad_frame = x0 < 0.005 or y0 < 0.002 or x1 > 0.995 or y1 > 0.998 or faces[0].area < 0.015
        fd = self._framing.update(bad_frame, ts)
        if fd >= self.min_framing_s:
            out.append(_sig('face_out_of_frame', Category.PRESENCE, Severity.LOW, 0.65, ts,
                            {'duration_s': round(fd, 1)}))
        return out


@register
class VideoQualityDetector(FrameDetector):
    name = 'video_quality'
    emits = ('camera_blocked', 'poor_video_quality')
    requires = frozenset({'meta'})

    def __init__(self, config=None):
        super().__init__(config)
        self._blocked = Sustained(off_grace_s=0.5)
        self._poor = Sustained(off_grace_s=2.0)
        self.min_width = self.config.get('min_width', 320)

    def reset(self):
        self._blocked.reset()
        self._poor.reset()

    def process(self, ctx: FrameContext):
        m, ts, out = ctx.get('meta'), ctx.frame.ts, []
        blocked = (m['brightness'] < 12 and m['contrast'] < 8) or (m['contrast'] < 4 and ctx.get('n_faces', 0) == 0)
        d = self._blocked.update(blocked, ts)
        if d >= 3.0:
            out.append(_sig('camera_blocked', Category.ENVIRONMENT, Severity.HIGH,
                            0.85 + 0.1 * min(1, d / 15), ts, {'brightness': round(m['brightness'], 1)}, evidence=True))
        poor = (not blocked) and (m['brightness'] < 40 or m['brightness'] > 235
                                  or m['sharpness'] < 15 or m['width'] < self.min_width)
        d = self._poor.update(poor, ts)
        if d >= 15.0:
            out.append(_sig('poor_video_quality', Category.ENVIRONMENT, Severity.LOW, 0.7, ts,
                            {'brightness': round(m['brightness'], 1), 'sharpness': round(m['sharpness'], 1),
                             'width': m['width']}))
        return out
