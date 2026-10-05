"""Event-driven detectors: browser telemetry, client integrity, connectivity, audio-visual sync, answer timing."""
from __future__ import annotations

import re
from collections import deque

import numpy as np

from ..framework.detector import Event, EventDetector, FrameContext, FrameDetector, register
from ..framework.types import Category, Severity, Signal, Source
from ..framework.util import clamp01

# Strong: software cameras whose only purpose is to inject video.
VIRTUAL_CAM_STRONG = re.compile(
    r'obs[\s\-]?(virtual|camera)|manycam|xsplit|snap camera|splitcam|vcam|virtual\s*cam|'
    r'unity video capture|e2esoft|alcor|fake\s*cam|chromacam|webcamoid|v4l2loopback|dummy', re.I)
# Weak: legitimate phone-as-webcam / effects apps that are also abused.
VIRTUAL_CAM_WEAK = re.compile(r'droidcam|iriun|epoccam|camo\b|nvidia broadcast|mmhmm|streamlabs|continuity', re.I)

SHORTCUT_BLOCKLIST = re.compile(
    r'^(alt\+tab|meta\+tab|cmd\+tab|printscreen|ctrl\+shift\+i|f12|ctrl\+shift\+j|ctrl\+u|ctrl\+shift\+s|meta\+shift\+s|'
    r'cmd\+shift\+[345]|alt\+f4|ctrl\+w|ctrl\+t|ctrl\+n)$', re.I)


def _sig(kind, cat, sev, conf, ts, details=None, evidence=False, source=Source.TELEMETRY):
    return Signal(kind=kind, category=cat, severity=sev, confidence=clamp01(conf), source=source,
                  details=details or {}, ts=ts, capture_evidence=evidence)


def classify_camera_label(label: str) -> tuple[str | None, float]:
    if VIRTUAL_CAM_STRONG.search(label or ''):
        return 'virtual', 0.92
    if VIRTUAL_CAM_WEAK.search(label or ''):
        return 'software_or_phone', 0.58
    return None, 0.0


@register
class BrowserTelemetryDetector(EventDetector):
    """Client-reported browser/OS context. Always treated as *untrusted corroboration*."""
    name = 'browser_telemetry'
    emits = ('tab_hidden', 'window_blur', 'fullscreen_exit', 'clipboard_paste', 'clipboard_copy',
             'context_menu', 'devtools_open', 'multiple_displays', 'virtual_camera', 'camera_changed',
             'blocked_shortcut', 'remote_access_suspected', 'client_integrity')
    handles = frozenset({'visibility', 'focus', 'fullscreen', 'clipboard', 'contextmenu', 'devtools',
                         'displays', 'camera', 'shortcut', 'env'})

    def __init__(self, config=None):
        super().__init__(config)
        self.blur_min_s = self.config.get('blur_min_s', 2.0)
        self._blur_start: float | None = None
        self._blur_reported = False
        self._camera_label: str | None = None
        self._fullscreen_seen = False

    def reset(self):
        self._blur_start = None
        self._blur_reported = False

    def process(self, e: Event):
        d, ts, k = e.data, e.ts, e.kind
        S = Severity
        if k == 'visibility':
            if d.get('hidden'):
                return [_sig('tab_hidden', Category.DEVICE, S.MEDIUM, 0.85, ts, {'event': 'hidden'}, True)]
        elif k == 'focus':
            if d.get('focused') is False:
                self._blur_start, self._blur_reported = ts, False
            elif self._blur_start is not None:
                dur = ts - self._blur_start
                self._blur_start = None
                if dur >= self.blur_min_s and not self._blur_reported:
                    return [_sig('window_blur', Category.DEVICE, S.LOW, 0.5 + min(0.4, dur / 30), ts,
                                 {'duration_s': round(dur, 1)})]
        elif k == 'fullscreen':
            if d.get('active'):
                self._fullscreen_seen = True
            elif self._fullscreen_seen:
                return [_sig('fullscreen_exit', Category.DEVICE, S.MEDIUM, 0.8, ts)]
        elif k == 'clipboard':
            act, n = d.get('action'), int(d.get('length', 0) or 0)
            if act == 'paste':
                return [_sig('clipboard_paste', Category.DEVICE, S.MEDIUM, 0.55 + min(0.4, n / 400), ts,
                             {'length': n}, True)]
            if act in ('copy', 'cut'):
                return [_sig('clipboard_copy', Category.DEVICE, S.LOW, 0.6, ts, {'action': act, 'length': n})]
        elif k == 'contextmenu':
            return [_sig('context_menu', Category.DEVICE, S.INFO, 0.5, ts)]
        elif k == 'devtools':
            if d.get('open'):
                return [_sig('devtools_open', Category.DEVICE, S.MEDIUM, 0.7, ts)]
        elif k == 'displays':
            if int(d.get('count', 1)) > 1 or d.get('extended'):
                return [_sig('multiple_displays', Category.DEVICE, S.MEDIUM, 0.7, ts, {'count': d.get('count')})]
        elif k == 'shortcut':
            combo = str(d.get('combo', '')).lower()
            if SHORTCUT_BLOCKLIST.match(combo):
                return [_sig('blocked_shortcut', Category.DEVICE, S.LOW, 0.6, ts, {'combo': combo})]
        elif k == 'camera':
            return self._camera(d, ts)
        elif k == 'env':
            return self._env(d, ts)
        return []

    def _camera(self, d, ts):
        out, label = [], str(d.get('label', ''))
        kind, conf = classify_camera_label(label)
        if kind and d.get('event') in ('selected', 'changed', None):
            out.append(_sig('virtual_camera', Category.TAMPERING, Severity.HIGH, conf, ts,
                            {'label': label[:80], 'class': kind}, True))
        if d.get('event') == 'changed' and self._camera_label and label != self._camera_label:
            out.append(_sig('camera_changed', Category.TAMPERING, Severity.MEDIUM, 0.75, ts,
                            {'from': self._camera_label[:80], 'to': label[:80]}))
        if label:
            self._camera_label = label
        if d.get('event') == 'ended':
            out.append(_sig('camera_changed', Category.TAMPERING, Severity.MEDIUM, 0.8, ts, {'reason': 'track_ended'}))
        return out

    def _env(self, d, ts):
        out = []
        if d.get('webdriver'):
            out.append(_sig('client_integrity', Category.TAMPERING, Severity.HIGH, 0.85, ts,
                            {'reason': 'navigator.webdriver (automation)'}))
        if d.get('remote_indicators'):
            out.append(_sig('remote_access_suspected', Category.DEVICE, Severity.HIGH, 0.7, ts,
                            {'indicators': list(d['remote_indicators'])[:5]}))
        if int(d.get('display_count', 1)) > 1:
            out.append(_sig('multiple_displays', Category.DEVICE, Severity.MEDIUM, 0.7, ts, {'count': d['display_count']}))
        return out

    def tick(self, now):
        if self._blur_start is not None and not self._blur_reported and now - self._blur_start >= 5.0:
            self._blur_reported = True
            return [_sig('window_blur', Category.DEVICE, Severity.LOW, 0.7, now,
                         {'duration_s': round(now - self._blur_start, 1), 'ongoing': True})]
        return []


@register
class ClientIntegrityDetector(EventDetector):
    """Transport-level tamper evidence + liveness of the proctoring client itself."""
    name = 'client_integrity'
    emits = ('client_integrity', 'client_silent', 'device_changed', 'concurrent_session')
    handles = frozenset({'transport'})

    def __init__(self, config=None):
        super().__init__(config)
        self.silent_after_s = self.config.get('silent_after_s', 30.0)
        self._last_seen: float | None = None
        self._silent_reported = False

    def on_activity(self, ts, kind, origin):
        if origin == 'client':
            self._last_seen, self._silent_reported = ts, False

    def process(self, e: Event):
        issue, ts, d = e.data.get('issue'), e.ts, e.data
        if issue in ('bad_signature', 'seq_replay', 'clock_skew'):
            return [_sig('client_integrity', Category.TAMPERING, Severity.HIGH, 0.8, ts, {'issue': issue}, source=Source.SYSTEM)]
        if issue == 'seq_gap':
            return [_sig('client_integrity', Category.TAMPERING, Severity.MEDIUM, 0.55, ts,
                         {'issue': issue, 'missing': d.get('missing')}, source=Source.SYSTEM)]
        if issue in ('ua_changed', 'ip_changed'):
            return [_sig('device_changed', Category.DEVICE, Severity.MEDIUM, 0.7 if issue == 'ua_changed' else 0.55, ts,
                         {'issue': issue}, source=Source.SYSTEM)]
        if issue == 'concurrent_session':
            return [_sig('concurrent_session', Category.DEVICE, Severity.HIGH, 0.9, ts, {}, True, Source.SYSTEM)]
        return []

    def tick(self, now):
        if (self._last_seen is not None and not self._silent_reported
                and now - self._last_seen >= self.silent_after_s):
            self._silent_reported = True
            return [_sig('client_silent', Category.TAMPERING, Severity.MEDIUM, 0.7, now,
                         {'silent_s': round(now - self._last_seen, 1)}, source=Source.SYSTEM)]
        return []


@register
class ConnectivityDetector(EventDetector):
    name = 'connectivity'
    emits = ('disconnected', 'excessive_reconnects')
    handles = frozenset({'connection'})

    def __init__(self, config=None):
        super().__init__(config)
        self.max_reconnects = self.config.get('max_reconnects', 6)
        self.reconnects = 0

    def process(self, e: Event):
        if e.data.get('state') != 'reconnected':
            return []
        self.reconnects += 1
        gap = float(e.data.get('gap_s', 0))
        out = [_sig('disconnected', Category.CONNECTIVITY, Severity.LOW, 0.3 + min(0.7, gap / 60.0), e.ts,
                    {'gap_s': round(gap, 1)}, source=Source.SYSTEM)]
        if self.reconnects > self.max_reconnects:
            out.append(_sig('excessive_reconnects', Category.CONNECTIVITY, Severity.MEDIUM, 0.8, e.ts,
                            {'reconnects': self.reconnects}, source=Source.SYSTEM))
        return out

    def export_state(self):
        return {'reconnects': self.reconnects}

    def import_state(self, state):
        self.reconnects = int(state.get('reconnects', 0))


@register
class AVSyncDetector(FrameDetector, EventDetector):
    """
    Voice with no matching mouth movement.

    Catches audio injected through a virtual cable (TTS / voice-clone / an
    assistant reading answers) or a second person speaking off-camera while the
    candidate sits silently. Needs the voice pipeline's VAD ('vad' events from
    the server) and the face's mouth activity. Requires a frontal, visible face
    so a turned head or a hand over the mouth does not trigger it.
    """
    name = 'av_sync'
    emits = ('av_desync',)
    handles = frozenset({'vad'})
    requires = frozenset({'primary'})

    def __init__(self, config=None):
        super().__init__(config)
        self.window_s = self.config.get('window_s', 8.0)
        self.min_speech_fraction = self.config.get('min_speech_fraction', 0.6)
        self.mouth_std_max = self.config.get('mouth_std_max', 0.02)
        self.mouth_peak_max = self.config.get('mouth_peak_max', 0.12)
        self._vad: deque = deque()                 # (ts, speech_bool) change log
        self._speech = False
        self._mouth: deque = deque()               # (ts, mouth)
        self._verdicts: deque = deque(maxlen=6)
        self._last_eval = 0.0

    def reset(self):
        self._vad.clear()
        self._mouth.clear()
        self._verdicts.clear()

    def process(self, ctx_or_event):
        if isinstance(ctx_or_event, Event):
            self._vad.append((ctx_or_event.ts, bool(ctx_or_event.data.get('speech'))))
            self._speech = bool(ctx_or_event.data.get('speech'))
            return []
        return self._on_frame(ctx_or_event)

    def _speech_fraction(self, t0, t1) -> float:
        total, state, last = 0.0, False, t0
        for ts, s in self._vad:
            if ts <= t0:
                state = s
                continue
            if ts > t1:
                break
            if state:
                total += ts - last
            state, last = s, ts
        if state:
            total += t1 - max(last, t0)
        return total / max(t1 - t0, 1e-6)

    def _on_frame(self, ctx: FrameContext):
        ts = ctx.frame.ts
        p = ctx.get('primary')
        a = ctx.get('attention')
        frontal = p is not None and ctx.get('n_faces') == 1 and (abs(a['yaw_dev']) < 25 if a else abs(p.yaw) < 25)
        if frontal:
            self._mouth.append((ts, p.mouth))
        while self._mouth and ts - self._mouth[0][0] > self.window_s:
            self._mouth.popleft()
        while len(self._vad) > 2 and self._vad[1][0] < ts - self.window_s - 5:
            self._vad.popleft()
        if ts - self._last_eval < 2.0 or len(self._mouth) < 8:
            return []
        self._last_eval = ts
        frac = self._speech_fraction(ts - self.window_s, ts)
        if frac < self.min_speech_fraction:
            return []
        m = np.array([v for _, v in self._mouth])
        desync = bool(m.std() < self.mouth_std_max and m.max() < self.mouth_peak_max)
        self._verdicts.append(desync)
        if len(self._verdicts) >= 4 and sum(self._verdicts) >= 4 and desync:
            return [_sig('av_desync', Category.BEHAVIOR, Severity.HIGH, 0.7 + 0.2 * (1 - float(m.max()) / self.mouth_peak_max), ts,
                         {'speech_fraction': round(frac, 2), 'mouth_std': round(float(m.std()), 4),
                          'mouth_peak': round(float(m.max()), 3)}, True, Source.VISION)]
        return []


@register
class ResponseLatencyDetector(EventDetector):
    """
    Near-constant delay before every answer (e.g. relaying questions to an
    assistant and reading its reply) is a published cheating signature; human
    latency varies with question difficulty. Needs >= 8 turns.
    """
    name = 'response_latency'
    emits = ('latency_signature',)
    handles = frozenset({'turn'})

    def __init__(self, config=None):
        super().__init__(config)
        self.min_samples = self.config.get('min_samples', 8)
        self.min_mean_s = self.config.get('min_mean_s', 2.0)
        self.max_cv = self.config.get('max_cv', 0.15)
        self._ai_end: float | None = None
        self.latencies: list[float] = []

    def process(self, e: Event):
        ph = e.data.get('phase')
        if ph == 'ai_end':
            self._ai_end = e.ts
        elif ph == 'user_start' and self._ai_end is not None:
            lat = e.ts - self._ai_end
            self._ai_end = None
            if 0.2 <= lat <= 30:
                self.latencies.append(lat)
                if len(self.latencies) >= self.min_samples:
                    arr = np.array(self.latencies[-self.min_samples * 2:])
                    cv = float(arr.std() / arr.mean())
                    if arr.mean() >= self.min_mean_s and cv <= self.max_cv:
                        return [_sig('latency_signature', Category.BEHAVIOR, Severity.MEDIUM,
                                     0.6 + 0.3 * (self.max_cv - cv) / self.max_cv, e.ts,
                                     {'mean_s': round(float(arr.mean()), 2), 'cv': round(cv, 3), 'n': len(arr)},
                                     source=Source.AUDIO)]
        return []

    def export_state(self):
        return {'latencies': self.latencies[-40:]}

    def import_state(self, state):
        self.latencies = list(state.get('latencies', []))
