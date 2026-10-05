import asyncio
import os
from pathlib import Path
import json
import struct
import time

import pytest
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.conf import settings

from api.routing import websocket_urlpatterns
from proctoring import consumers, services
from proctoring.framework import tokens
from proctoring.models import ProctorSession

from proctoring.testing import make_jpeg

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]
APP = URLRouter(websocket_urlpatterns)


@pytest.fixture
def started(make_session):
    """Active session with no grace period so soft signals count immediately in tests."""
    def _mk(policy='standard', **ov):
        s = make_session(policy, overrides={'grace_period_s': 0, **ov})
        services.record_consent(s.id, ip='127.0.0.1')
        services.start_session(s.id, ip='127.0.0.1', user_agent='UA')
        creds = services.issue_credentials(s)
        return str(s.id), creds
    return _mk


def connect(sid, token, path=None):
    c = WebsocketCommunicator(APP, path or f'/ws/proctor/{sid}/', subprotocols=['proctor.v1', token])
    return c


def batch(sid, seq, events):
    payload = json.dumps(events)
    key = tokens.derive_session_key(settings.PROCTOR['TOKEN_SECRET'], sid)
    return {'type': 'events', 'seq': seq, 'payload': payload, 'sig': tokens.sign_batch(key, sid, seq, payload)}


async def drain(comm, timeout=0.4):
    """Collect everything pending. (receive_json_from(timeout) would *cancel* the app on timeout.)"""
    out = []
    while not await comm.receive_nothing(timeout=timeout):
        msg = await comm.receive_output(timeout=2)
        if msg['type'] == 'websocket.send' and msg.get('text'):
            out.append(json.loads(msg['text']))
        elif msg['type'] == 'websocket.close':
            out.append({'type': '_closed', 'code': msg.get('code')})
            break
    return out


async def db(fn, *a, **k):
    return await database_sync_to_async(fn)(*a, **k)


def events_of(sid):
    return list(ProctorSession.objects.get(pk=sid).events.filter(counted=True).values_list('kind', flat=True))


async def test_connect_ready_and_reject_bad_tokens(started):
    sid, creds = await db(started)
    comm = connect(sid, creds['candidate_token'])
    ok, sub = await comm.connect()
    assert ok and sub == 'proctor.v1'
    ready = await comm.receive_json_from()
    assert ready['type'] == 'ready' and ready['state'] == 'active' and ready['max_strikes'] == 3
    await comm.disconnect()

    for bad in ('garbage', tokens.issue('wrong-secret', sid, 'candidate', 60)):
        c = connect(sid, bad)
        ok, code = await c.connect()
        assert not ok and code == 4001

    other = await db(started)
    c = connect(sid, other[1]['candidate_token'])                          # valid token for a different session
    ok, code = await c.connect()
    assert not ok and code == 4004


async def test_signed_telemetry_counts_and_bad_inputs_are_evidence(started):
    sid, creds = await db(started)
    comm = connect(sid, creds['candidate_token'])
    await comm.connect(); await comm.receive_json_from()

    await comm.send_json_to(batch(sid, 0, [{'kind': 'visibility', 'ts': 1, 'data': {'hidden': True}}]))
    await drain(comm)
    assert 'tab_hidden' in await db(events_of, sid)

    # replay of the same sequence number
    await comm.send_json_to(batch(sid, 0, [{'kind': 'devtools', 'ts': 2, 'data': {'open': True}}]))
    # forged signature
    forged = batch(sid, 5, [{'kind': 'devtools', 'ts': 2, 'data': {'open': True}}]); forged['sig'] = 'AAAA'
    await comm.send_json_to(forged)
    await drain(comm)
    ev = await db(events_of, sid)
    assert ev.count('client_integrity') >= 1 and 'devtools_open' not in ev        # forged/replayed content never applied
    await comm.disconnect()


async def test_unknown_event_kinds_and_oversized_payloads_are_dropped(started):
    sid, creds = await db(started)
    comm = connect(sid, creds['candidate_token'])
    await comm.connect(); await comm.receive_json_from()
    await comm.send_json_to(batch(sid, 0, [{'kind': 'rm -rf', 'data': {}}, {'kind': 'visibility', 'data': {'hidden': True, 'blob': 'x' * 5000}}]))
    await drain(comm)
    assert await db(events_of, sid) == []
    await comm.disconnect()


async def test_escalation_to_termination_notifies_and_closes(started):
    sid, creds = await db(started, 'strict')
    comm = connect(sid, creds['candidate_token'])
    await comm.connect(); await comm.receive_json_from()
    await comm.send_json_to(batch(sid, 0, [{'kind': 'camera', 'ts': 1, 'data': {'event': 'selected', 'label': 'OBS Virtual Camera'}}]))
    msgs = await drain(comm, 0.6)
    terminated = [m for m in msgs if m['type'] == 'terminated']
    assert terminated and 'virtual' not in json.dumps(terminated[0]).lower() and 'OBS' not in json.dumps(msgs)
    s = await db(ProctorSession.objects.get, pk=sid)
    assert s.state == 'terminated' and s.verdict == 'fail'
    assert any(m == {'type': '_closed', 'code': 4003} for m in msgs)
    await comm.disconnect()


async def test_warning_notice_is_generic_and_includes_strike_counts(started):
    sid, creds = await db(started)
    comm = connect(sid, creds['candidate_token'])
    await comm.connect(); await comm.receive_json_from()
    await comm.send_json_to(batch(sid, 0, [{'kind': 'visibility', 'ts': 1, 'data': {'hidden': True}}]))
    msgs = await drain(comm, 0.6)
    notice = next(m for m in msgs if m['type'] == 'notice')
    assert notice['strikes'] == 1 and notice['max_strikes'] == 3 and 'tab' not in notice['message'].lower()
    await comm.disconnect()


async def test_frames_are_accepted_or_dropped_safely_without_models(started, monkeypatch):
    from proctoring import runtime
    monkeypatch.setattr(runtime, '_extractor', None); monkeypatch.setattr(runtime, '_extractor_failed', True)
    sid, creds = await db(started)
    comm = connect(sid, creds['candidate_token'])
    await comm.connect()
    ready = await comm.receive_json_from()
    assert ready['capabilities']['video'] is False
    jpeg = make_jpeg()
    await comm.send_to(bytes_data=struct.pack('!Id', 1, time.time()) + jpeg)
    await comm.send_to(bytes_data=b'\x00' * 5)                                  # too short
    await comm.send_to(bytes_data=struct.pack('!Id', 2, 0.0) + b'not a jpeg at all....')
    await comm.send_to(bytes_data=struct.pack('!Id', 3, 0.0) + b'\xff' * (settings.PROCTOR['MAX_FRAME_BYTES'] + 1))
    await drain(comm)
    await comm.send_json_to({'type': 'heartbeat'})
    await drain(comm)
    await comm.disconnect()
    assert (await db(ProctorSession.objects.get, pk=sid)).state == 'paused'


async def test_disconnect_pauses_and_reconnect_resumes_with_gap_recorded(started):
    sid, creds = await db(started)
    c1 = connect(sid, creds['candidate_token'])
    await c1.connect(); await c1.receive_json_from()
    await c1.disconnect()
    s = await db(ProctorSession.objects.get, pk=sid)
    assert s.state == 'paused' and s.disconnected_at

    c2 = connect(sid, creds['candidate_token'])
    await c2.connect()
    msgs = [await c2.receive_json_from()] + await drain(c2)
    s = await db(ProctorSession.objects.get, pk=sid)
    assert s.state == 'active' and s.reconnect_count == 1
    assert 'disconnected' in await db(events_of, sid)
    await c2.disconnect()


async def test_second_connection_replaces_first(started):
    sid, creds = await db(started)
    c1, c2 = connect(sid, creds['candidate_token']), connect(sid, creds['candidate_token'])
    await c1.connect(); await c1.receive_json_from()
    await c2.connect(); await c2.receive_json_from()
    msgs = await drain(c1)
    assert {'type': '_closed', 'code': 4009} in msgs and any(m.get('code') == 'replaced' for m in msgs)
    await c1.disconnect()
    assert consumers.RUNTIMES[sid][1] == 1                       # no leaked reference from the replaced socket
    active_before = consumers._active
    await c2.disconnect()
    assert sid not in consumers.RUNTIMES and consumers._active == active_before - 1


async def test_ended_sessions_refuse_connections(started):
    sid, creds = await db(started)
    await db(services.complete_session, sid)
    ok, code = await connect(sid, creds['candidate_token']).connect()
    assert not ok and code == 4010


async def test_client_can_complete_session_over_the_socket(started):
    sid, creds = await db(started)
    comm = connect(sid, creds['candidate_token'])
    await comm.connect(); await comm.receive_json_from()
    await comm.send_json_to({'type': 'complete'})
    msgs = await drain(comm)
    assert any(m['type'] == 'completed' for m in msgs)
    assert (await db(ProctorSession.objects.get, pk=sid)).state == 'completed'


@pytest.mark.skipif(not (os.environ.get('PROCTOR_TEST_PORTRAIT') and Path(os.environ.get('PROCTOR_TEST_PORTRAIT', '')).exists()
                         and Path(settings.PROCTOR['MODEL_DIR'], 'face_landmarker.task').exists()),
                    reason='needs models + PROCTOR_TEST_PORTRAIT')
async def test_real_video_over_websocket_calibrates_and_persists_state(started, monkeypatch):
    import cv2
    from proctoring import runtime
    runtime.reset_extractor_for_tests()
    sid, creds = await db(started, calibration_s=3)
    img = cv2.imread(os.environ['PROCTOR_TEST_PORTRAIT'])
    img = cv2.resize(img, (640, int(640 * img.shape[0] / img.shape[1])))
    ok, jpeg = cv2.imencode('.jpg', img)
    comm = connect(sid, creds['candidate_token'])
    await comm.connect()
    ready = await comm.receive_json_from()
    assert ready['capabilities']['video'] and ready['capabilities']['faces']
    status = []
    for i in range(14):
        await comm.send_to(bytes_data=struct.pack('!Id', i, time.time()) + jpeg.tobytes())
        await asyncio.sleep(0.45)                                           # ~2 fps, real time
        status += [m for m in await drain(comm, 0.05) if m['type'] == 'status']
    assert status and status[-1]['faces'] == 1
    await comm.disconnect()
    s = await db(ProctorSession.objects.get, pk=sid)
    assert s.baseline.get('ready') is True and s.frames_analyzed >= 5        # calibration survives reconnects
    runtime.reset_extractor_for_tests()
