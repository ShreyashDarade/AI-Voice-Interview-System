import numpy as np
import pytest

import proctoring.detectors  # noqa: F401
from proctoring.detectors.events import (AVSyncDetector, BrowserTelemetryDetector, ClientIntegrityDetector,
                                         ConnectivityDetector, ResponseLatencyDetector, classify_camera_label)
from proctoring.framework.detector import Event, FeatureExtractor, FrameDetector, register
from proctoring.framework.pipeline import Pipeline
from proctoring.framework.policy import get_policy
from proctoring.framework.types import Category, Frame, Severity, Signal, Source

from .helpers import ctx, face


def ev(kind, ts=100.0, origin='client', **data):
    return Event(kind=kind, ts=ts, data=data, origin=origin)


def kinds(s):
    return [x.kind for x in s]


# ============================================================================ browser telemetry
class TestBrowserTelemetry:
    def test_tab_hidden(self):
        s = BrowserTelemetryDetector().process(ev('visibility', hidden=True))
        assert kinds(s) == ['tab_hidden'] and s[0].capture_evidence
        assert not BrowserTelemetryDetector().process(ev('visibility', hidden=False))

    def test_blur_only_counts_when_long(self):
        d = BrowserTelemetryDetector()
        d.process(ev('focus', 100, focused=False))
        assert not d.process(ev('focus', 100.8, focused=True))                      # notification pop-up
        d.process(ev('focus', 200, focused=False))
        s = d.process(ev('focus', 212, focused=True))
        assert kinds(s) == ['window_blur'] and s[0].details['duration_s'] == 12

    def test_ongoing_blur_is_reported_by_tick_once(self):
        d = BrowserTelemetryDetector()
        d.process(ev('focus', 100, focused=False))
        assert not d.tick(102)
        assert kinds(d.tick(106)) == ['window_blur']
        assert not d.tick(120)
        assert not d.process(ev('focus', 130, focused=True))                         # already reported

    def test_fullscreen_exit_only_after_entering(self):
        d = BrowserTelemetryDetector()
        assert not d.process(ev('fullscreen', active=False))
        d.process(ev('fullscreen', active=True))
        assert kinds(d.process(ev('fullscreen', active=False))) == ['fullscreen_exit']

    def test_paste_confidence_grows_with_length(self):
        d = BrowserTelemetryDetector()
        small = d.process(ev('clipboard', action='paste', length=5))[0].confidence
        big = d.process(ev('clipboard', action='paste', length=900))[0].confidence
        assert big > small and big <= 1.0
        assert kinds(d.process(ev('clipboard', action='copy', length=3))) == ['clipboard_copy']

    @pytest.mark.parametrize('label,expected', [
        ('OBS Virtual Camera', 'virtual'), ('ManyCam Virtual Webcam', 'virtual'), ('Snap Camera', 'virtual'),
        ('XSplit VCam', 'virtual'), ('DroidCam Source 3', 'software_or_phone'), ('Camo', 'software_or_phone'),
        ('NVIDIA Broadcast', 'software_or_phone'), ('Logitech C920 HD Pro Webcam', None),
        ('FaceTime HD Camera (Built-in)', None), ('Integrated Camera', None)])
    def test_camera_label_classification(self, label, expected):
        assert classify_camera_label(label)[0] == expected

    def test_virtual_camera_confidence_distinguishes_phone_webcam_apps(self):
        d = BrowserTelemetryDetector()
        strong = d.process(ev('camera', event='selected', label='OBS Virtual Camera'))[0]
        weak = BrowserTelemetryDetector().process(ev('camera', event='selected', label='DroidCam Source 3'))[0]
        assert strong.confidence > 0.85 > weak.confidence
        assert not BrowserTelemetryDetector().process(ev('camera', event='selected', label='Logitech C920'))

    def test_camera_swap_mid_session(self):
        d = BrowserTelemetryDetector()
        d.process(ev('camera', event='selected', label='Logitech C920'))
        assert 'camera_changed' in kinds(d.process(ev('camera', event='changed', label='Integrated Camera')))

    def test_automation_and_remote_hints(self):
        s = BrowserTelemetryDetector().process(ev('env', webdriver=True, remote_indicators=['rdp'], display_count=2))
        assert set(kinds(s)) == {'client_integrity', 'remote_access_suspected', 'multiple_displays'}

    def test_shortcuts_and_devtools(self):
        d = BrowserTelemetryDetector()
        assert kinds(d.process(ev('shortcut', combo='Alt+Tab'))) == ['blocked_shortcut']
        assert not d.process(ev('shortcut', combo='ctrl+b'))
        assert kinds(d.process(ev('devtools', open=True))) == ['devtools_open']


class TestClientIntegrity:
    def test_transport_issues(self):
        d = ClientIntegrityDetector()
        assert d.process(ev('transport', issue='bad_signature', origin='server'))[0].kind == 'client_integrity'
        assert d.process(ev('transport', issue='seq_gap', missing=3, origin='server'))[0].confidence < 0.7
        assert d.process(ev('transport', issue='ua_changed', origin='server'))[0].kind == 'device_changed'
        assert d.process(ev('transport', issue='concurrent_session', origin='server'))[0].kind == 'concurrent_session'

    def test_silent_client_detected_only_after_it_was_seen(self):
        d = ClientIntegrityDetector({'silent_after_s': 30})
        assert not d.tick(1000)                                  # never seen: no baseline to compare
        d.on_activity(1000, 'heartbeat', 'client')
        d.on_activity(1010, 'vad', 'server')                     # server events don't prove the client is alive
        assert not d.tick(1020)
        assert kinds(d.tick(1031)) == ['client_silent'] and not d.tick(1100)
        d.on_activity(1200, 'frame', 'client')
        assert kinds(d.tick(1240)) == ['client_silent']          # re-armed after activity


class TestConnectivity:
    def test_excessive_reconnects(self):
        d = ConnectivityDetector({'max_reconnects': 2})
        out = []
        for i in range(4):
            out += d.process(ev('connection', 100 + i, origin='server', state='reconnected', gap_s=20))
        assert kinds(out).count('disconnected') == 4 and kinds(out).count('excessive_reconnects') == 2
        st = d.export_state(); d2 = ConnectivityDetector(); d2.import_state(st)
        assert d2.reconnects == 4


# ============================================================================ audio-visual
class TestAVSync:
    def drive(self, mouth_fn, speech=True, seconds=40, yaw=0.0):
        d = AVSyncDetector()
        out = []
        if speech:
            d.process(Event('vad', 0.0, {'speech': True}, 'server'))
        for i in range(seconds * 2):
            t = 0.5 * i
            out += d.process(ctx(t, faces=[face(mouth=mouth_fn(t), yaw=yaw)]))
        return out

    def test_speech_with_closed_mouth_flagged(self):
        assert 'av_desync' in kinds(self.drive(lambda t: 0.02))

    def test_speech_with_moving_mouth_is_fine(self):
        rng = np.random.default_rng(0)
        assert not self.drive(lambda t: 0.1 + 0.25 * abs(np.sin(5 * t)) + 0.05 * rng.random())

    def test_silence_with_closed_mouth_is_fine(self):
        assert not self.drive(lambda t: 0.02, speech=False)

    def test_turned_head_does_not_trigger(self):
        assert not self.drive(lambda t: 0.02, yaw=60)


class TestResponseLatency:
    def run_turns(self, latencies):
        d, out, t = ResponseLatencyDetector(), [], 0.0
        for lat in latencies:
            out += d.process(ev('turn', t, origin='server', phase='ai_end'))
            out += d.process(ev('turn', t + lat, origin='server', phase='user_start'))
            t += lat + 20
        return out

    def test_metronomic_delay_flagged(self):
        assert 'latency_signature' in kinds(self.run_turns([3.1, 3.0, 3.2, 3.1, 3.0, 3.1, 3.2, 3.0, 3.1]))

    def test_human_variation_not_flagged(self):
        assert not self.run_turns([0.9, 4.2, 1.5, 6.8, 2.2, 0.7, 3.9, 5.5, 1.1, 2.8])

    def test_instant_uniform_answers_not_flagged(self):
        assert not self.run_turns([0.5] * 10)           # fast natural conversation


# ============================================================================ pipeline
class FakeExtractor(FeatureExtractor):
    name = 'fake'
    provides = frozenset()

    def __init__(self, feats_fn):
        self.fn, self.calls = feats_fn, 0

    def extract(self, frame):
        self.calls += 1
        return self.fn(frame)


def feats(frame, faces=None):
    c = ctx(frame.ts, faces=faces)
    return dict(c.features)


class TestPipeline:
    def test_attention_detectors_wait_for_calibration(self):
        pol = get_policy('standard', {'calibration_s': 6})
        pipe = Pipeline(pol, FakeExtractor(lambda f: {k: v for k, v in feats(f, [face(yaw=45)]).items() if k != 'attention'}))
        sigs = []
        for i in range(10):                                     # 5 s: not yet calibrated
            sigs += pipe.process_frame(Frame(image=None, ts=100 + i * 0.5)).signals
        assert not pipe.baseline.ready and not sigs

    def test_failing_detector_is_isolated(self, monkeypatch):
        from proctoring.framework import detector as registry
        monkeypatch.setattr(registry, '_DETECTORS', dict(registry._DETECTORS))      # keep the global registry clean

        @register
        class Boom(FrameDetector):
            name = 'boom_test'
            emits = ()

            def process(self, c):
                raise RuntimeError('boom')

        import dataclasses
        pol = dataclasses.replace(get_policy('standard'), detectors=('boom_test', 'face_presence'))
        pipe = Pipeline(pol, FakeExtractor(lambda f: feats(f, [])))
        out = []
        for i in range(20):
            out += pipe.process_frame(Frame(image=None, ts=100 + i * 0.5)).signals
        assert 'face_missing' in kinds(out)                   # healthy detector still worked
        assert pipe.stats['boom_test']['errors'] == 20

    def test_extractor_failure_does_not_raise(self):
        def bad(f):
            raise ValueError('model crashed')
        r = Pipeline(get_policy('standard'), FakeExtractor(bad)).process_frame(Frame(image=None, ts=1))
        assert r.error == 'extract_failed:ValueError' and not r.signals

    def test_events_routed_by_handles_and_state_exported(self):
        pipe = Pipeline(get_policy('standard'), None)
        assert kinds(pipe.process_event(Event('visibility', 1, {'hidden': True}))) == ['tab_hidden']
        assert kinds(pipe.process_event(Event('connection', 2, {'state': 'reconnected', 'gap_s': 5}, 'server'))) == ['disconnected']
        state = pipe.export_state()
        assert state['detectors']['connectivity']['reconnects'] == 1
        restored = Pipeline(get_policy('standard'), None, state=state)
        assert restored.detector('connectivity').reconnects == 1

    def test_policy_controls_which_detectors_exist(self):
        pol = get_policy('standard', {'accommodations': ['relaxed_gaze'], 'disable_detectors': ['objects']})
        names = {d.name for d in Pipeline(pol, None).detectors}
        assert 'gaze' not in names and 'objects' not in names and 'face_presence' in names

    def test_detector_config_reaches_detectors(self):
        pipe = Pipeline(get_policy('strict'), None)
        assert pipe.detector('face_presence').min_missing_s == 2.5
        assert Pipeline(get_policy('lenient'), None).detector('face_presence').min_missing_s == 8.0
