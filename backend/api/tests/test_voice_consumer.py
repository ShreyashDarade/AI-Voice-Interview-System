"""Voice socket with a fake Gemini client: gating, routing and resumability (no network)."""
import asyncio
import json
import struct
import time

import numpy as np
import pytest
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator

from api import consumers as voice
from api.routing import websocket_urlpatterns
from proctoring import services
from proctoring.models import ProctorSession
from proctoring.testing import make_jpeg

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]
APP = URLRouter(websocket_urlpatterns)


class FakeGemini:
    instances = []

    def __init__(self, **kw):
        self.kw = kw
        self.ai_is_speaking = False
        self.audio, self.texts, self.turns, self.closed = [], [], 0, False
        self.resumption_handle = kw.get('resumption_handle')
        FakeGemini.instances.append(self)

    async def connect(self):
        self.resumption_handle = self.resumption_handle or 'handle-1'

    async def receive_loop(self):
        await asyncio.sleep(3600)

    async def send_audio(self, b):
        self.audio.append(b)

    async def send_text(self, t):
        self.texts.append(t)

    async def send_turn_complete(self):
        self.turns += 1

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fake_gemini(monkeypatch):
    FakeGemini.instances.clear()
    monkeypatch.setattr(voice, 'GeminiLiveClient', FakeGemini)


@pytest.fixture
def started(make_session):
    def _mk(start=True, **ov):
        s = make_session('standard', overrides={'grace_period_s': 0, **ov})
        services.record_consent(s.id, ip='127.0.0.1')
        if start:
            services.start_session(s.id, ip='127.0.0.1', user_agent='UA')
        return s, services.issue_credentials(s)
    return _mk


def open_socket(s, creds):
    return WebsocketCommunicator(APP, f'/ws/interview/{s.interview_id}/', subprotocols=['proctor.v1', creds['candidate_token']])


async def drain(comm, timeout=0.3):
    out = []
    while not await comm.receive_nothing(timeout=timeout):
        m = await comm.receive_output(timeout=2)
        if m['type'] == 'websocket.send' and m.get('text'):
            out.append(json.loads(m['text']))
        elif m['type'] == 'websocket.close':
            out.append({'type': '_closed', 'code': m.get('code')})
            break
        elif m['type'] == 'websocket.send':
            out.append({'type': '_binary'})
    return out


async def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return cond()


def loud(n=1600, seed=0):
    rng = np.random.default_rng(seed)
    x = np.convolve(rng.normal(0, 0.4, n + 1), [0.5, 0.5], mode='valid')
    return np.clip(x, -1, 1).astype(np.float32).tobytes()


def quiet(n=1600, seed=1):
    return (np.random.default_rng(seed).normal(0, 0.0005, n)).astype(np.float32).tobytes()


async def test_voice_requires_started_session(started):
    s, creds = await database_sync_to_async(started)(start=False)
    ok, code = await open_socket(s, creds).connect()
    assert not ok and code == 4012


async def test_speech_is_forwarded_and_end_of_turn_signalled(started):
    s, creds = await database_sync_to_async(started)()
    comm = open_socket(s, creds)
    ok, _ = await comm.connect()
    assert ok
    first = await drain(comm)
    assert {m['type'] for m in first} >= {'ready', 'connected'}
    fake = FakeGemini.instances[0]
    for i in range(60):                                     # learn the noise floor
        await comm.send_to(bytes_data=quiet(seed=i))
    for i in range(20):
        await comm.send_to(bytes_data=loud(seed=i))
    for i in range(60):                                     # VAD hysteresis eats 12 frames, then 30 silent frames end the turn
        await comm.send_to(bytes_data=quiet(seed=100 + i))
    assert await wait_for(lambda: fake.turns >= 1)
    assert fake.audio and all(isinstance(a, bytes) for a in fake.audio)
    assert fake.turns == 1
    await comm.disconnect()


async def test_mic_audio_is_ignored_while_the_interviewer_speaks(started):
    s, creds = await database_sync_to_async(started)()
    comm = open_socket(s, creds)
    await comm.connect(); await drain(comm)
    fake = FakeGemini.instances[0]
    fake.ai_is_speaking = True
    for i in range(30):
        await comm.send_to(bytes_data=loud(seed=i))
    await drain(comm, 0.2)
    assert fake.audio == [] and fake.turns == 0
    await comm.disconnect()


async def test_frames_use_the_PXF1_prefix_and_garbage_does_not_crash(started, monkeypatch):
    from proctoring import runtime
    monkeypatch.setattr(runtime, '_extractor', None); monkeypatch.setattr(runtime, '_extractor_failed', True)
    s, creds = await database_sync_to_async(started)()
    comm = open_socket(s, creds)
    await comm.connect(); await drain(comm)
    await comm.send_to(bytes_data=b'PXF1' + struct.pack('!Id', 1, time.time()) + make_jpeg())
    await comm.send_to(bytes_data=b'PXF1' + b'short')
    await comm.send_to(text_data='{not json')
    await comm.send_to(text_data=json.dumps({'type': 'audio', 'data': '!!!notbase64'}))
    msgs = await drain(comm, 0.2)
    assert any(m.get('code') == 'bad_message' for m in msgs)
    assert (await database_sync_to_async(ProctorSession.objects.get)(pk=s.id)).state == 'active'
    await comm.disconnect()


async def test_legacy_client_trusted_cheating_messages_are_ignored(started):
    """The old `cheating_detected` message let any client self-report (or forge) strikes. It no longer does anything."""
    s, creds = await database_sync_to_async(started)()
    comm = open_socket(s, creds)
    await comm.connect(); await drain(comm)
    await comm.send_json_to({'type': 'cheating_detected', 'confidence': 1.0})
    await drain(comm, 0.2)
    assert (await database_sync_to_async(ProctorSession.objects.get)(pk=s.id)).strikes == 0
    await comm.disconnect()


async def test_disconnect_keeps_interview_resumable_and_persists_ai_session(started):
    s, creds = await database_sync_to_async(started)()
    comm = open_socket(s, creds)
    await comm.connect(); await drain(comm)
    fake = FakeGemini.instances[0]
    await comm.disconnect()
    assert fake.closed
    sess = await database_sync_to_async(ProctorSession.objects.select_related('interview').get)(pk=s.id)
    assert sess.state == 'paused' and sess.interview.status == 'in_progress'               # NOT ended on socket drop
    assert sess.interview.session_data['gemini_resumption_handle'] == 'handle-1'

    comm2 = open_socket(s, creds)
    await comm2.connect(); await drain(comm2)
    assert FakeGemini.instances[1].kw['resumption_handle'] == 'handle-1'                  # same AI conversation continues
    assert (await database_sync_to_async(ProctorSession.objects.get)(pk=s.id)).state == 'active'
    await comm2.disconnect()


async def test_end_interview_completes_the_session(started):
    s, creds = await database_sync_to_async(started)()
    comm = open_socket(s, creds)
    await comm.connect(); await drain(comm)
    await comm.send_json_to({'type': 'end_interview'})
    msgs = await drain(comm, 0.3)
    assert any(m['type'] == 'completed' for m in msgs)
    sess = await database_sync_to_async(ProctorSession.objects.select_related('interview').get)(pk=s.id)
    assert sess.state == 'completed' and sess.interview.status == 'completed'
