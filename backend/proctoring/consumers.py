"""
WebSocket transport for proctoring.

``ProctorMixin`` implements everything session-related (auth, frames, signed
telemetry batches, back-pressure, escalation) so the dedicated proctoring
socket and the voice-interview socket behave identically.

Wire protocol (v1)
------------------
client -> server
  binary : 12-byte header ``!Id`` (uint32 frame_seq, float64 client_time_s) + JPEG bytes
  text   : {"type":"events","seq":int,"sig":str,"payload":str}   payload = JSON array of
           {"kind","ts","data"}; sig = HMAC-SHA256(telemetry_key, "<session>:<seq>:"+payload)
           {"type":"heartbeat"} | {"type":"complete"}
server -> client
  {"type":"ready"|"status"|"notice"|"terminated"|"error", ...}
Auth: ``Sec-WebSocket-Protocol: proctor.v1, <candidate_token>`` (preferred) or ``?token=``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from . import runtime as rt
from . import services
from .framework import tokens
from .framework.detector import Event
from .framework.types import Action
from .models import ProctorSession

logger = logging.getLogger(__name__)

SUBPROTOCOL = 'proctor.v1'
ALLOWED_EVENT_KINDS = frozenset({'visibility', 'focus', 'fullscreen', 'clipboard', 'contextmenu', 'devtools',
                                 'displays', 'camera', 'shortcut', 'env'})
MAX_BATCH_EVENTS = 50
MAX_PAYLOAD_BYTES = 32_768
MAX_EVENT_DATA_BYTES = 2_048
STATE_SAVE_INTERVAL_S = 30
TICK_INTERVAL_S = 5

_executor: ThreadPoolExecutor | None = None
RUNTIMES: dict[str, list] = {}          # session_id -> [SessionRuntime, refcount]
CONNECTIONS: dict[str, 'ProctorMixin'] = {}
_active = 0


def executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=max(1, settings.PROCTOR['POOL_SIZE']), thread_name_prefix='proctor')
    return _executor


def extract_token(scope) -> str | None:
    subs = scope.get('subprotocols') or []
    if SUBPROTOCOL in subs:
        others = [x for x in subs if x != SUBPROTOCOL]
        if others:
            return others[0]
    q = parse_qs((scope.get('query_string') or b'').decode())
    return (q.get('token') or [None])[0]


def verify_events(raw_events, now: float) -> list[dict]:
    """Validate/sanitise a decoded events array. Unknown kinds and oversized data are dropped."""
    out = []
    if not isinstance(raw_events, list):
        return out
    for e in raw_events[:MAX_BATCH_EVENTS]:
        if not isinstance(e, dict) or e.get('kind') not in ALLOWED_EVENT_KINDS:
            continue
        data = e.get('data') if isinstance(e.get('data'), dict) else {}
        if len(json.dumps(data, default=str)) > MAX_EVENT_DATA_BYTES:
            continue
        out.append({'kind': e['kind'], 'data': {**data, 'client_ts': e.get('ts')}})
    return out


class ProctorMixin:
    session_id: str | None = None
    runtime: 'rt.SessionRuntime | None' = None

    # -- lifecycle ----------------------------------------------------------------
    async def proctor_connect(self) -> bool:
        """Authenticate + attach to the session. Returns False (and closes) on failure."""
        global _active
        cfg = settings.PROCTOR
        url_id = self.scope['url_route']['kwargs'].get('session_id') or self.scope['url_route']['kwargs'].get('interview_id')
        token = extract_token(self.scope)
        try:
            claims = tokens.verify(cfg['TOKEN_SECRET'], token or '', 'candidate')
        except tokens.TokenError:
            await self.close(code=4001)
            return False
        sid = claims['sub']
        s = await database_sync_to_async(self._load_session)(sid, url_id)
        if s is None:
            await self.close(code=4004)
            return False
        if s.is_terminal:
            await self.close(code=4010)
            return False
        if _active >= settings.MAX_CONCURRENT_INTERVIEWS:
            await self.close(code=4429)
            return False

        self.session_id = sid
        # a second socket for the same session replaces the first (and is itself evidence)
        prev = CONNECTIONS.get(sid)
        if prev is not None and prev is not self:
            await prev.proctor_kick()
        CONNECTIONS[sid] = self
        _active += 1
        self._counted = True

        if s.state == ProctorSession.State.PAUSED:
            _, gap = await database_sync_to_async(services.connection_restored)(sid, ip=self._client_ip())
            self._pending_events = [('connection', {'state': 'reconnected', 'gap_s': gap}, 'server')]
        else:
            self._pending_events = []

        entry = RUNTIMES.get(sid)
        if entry is None:
            runtime = await database_sync_to_async(rt.build_runtime)(s)
            entry = RUNTIMES[sid] = [runtime, 0]
        entry[1] += 1
        self.runtime = entry[0]
        self._frame_lock = asyncio.Lock()
        self._frames_seen = 0
        self._last_status = 0.0
        self._last_hb_db = 0.0
        self._closing = False
        self._ticker = asyncio.create_task(self._tick_loop())
        return True

    async def proctor_ready_message(self):
        s = await database_sync_to_async(ProctorSession.objects.get)(pk=self.session_id)
        msg = {'type': 'ready', **services.candidate_view(s), 'capabilities': rt.capabilities(),
               'calibrated': self.runtime.pipeline.baseline.ready}
        await self.send(text_data=json.dumps(msg))
        for kind, data, origin in self._pending_events:
            await self.proctor_event(kind, data, origin)
        self._pending_events = []

    async def proctor_disconnect(self):
        global _active
        if self.session_id is None or not getattr(self, '_counted', False):
            return
        self._counted = False
        _active = max(0, _active - 1)
        self._closing = True
        t = getattr(self, '_ticker', None)
        if t:
            t.cancel()
        sid = self.session_id
        if CONNECTIONS.get(sid) is self:
            CONNECTIONS.pop(sid, None)
            entry = RUNTIMES.get(sid)
            if entry:
                entry[1] -= 1
                if entry[1] <= 0:
                    RUNTIMES.pop(sid, None)
                await database_sync_to_async(services.save_runtime_state)(
                    sid, entry[0].pipeline.export_state(), entry[0].frames, entry[0].dropped)
            await database_sync_to_async(services.connection_lost)(sid)
        else:                                   # replaced by a newer socket: it owns the runtime now
            entry = RUNTIMES.get(sid)
            if entry:
                entry[1] -= 1

    async def proctor_kick(self):
        # bookkeeping (active counter, runtime refcount) happens in disconnect(), which always follows close()
        await self.send(text_data=json.dumps({'type': 'error', 'code': 'replaced',
                                              'message': 'Session opened elsewhere.'}))
        await self.close(code=4009)

    @staticmethod
    def _load_session(sid, url_id):
        s = ProctorSession.objects.filter(pk=sid).first()
        if s is None:
            return None
        if url_id and str(url_id) not in (str(s.id), str(s.interview_id)):
            return None
        return s

    def _client_ip(self):
        client = self.scope.get('client')
        return client[0] if client else None

    # -- inbound ----------------------------------------------------------------
    async def proctor_binary(self, data: bytes):
        cfg = settings.PROCTOR
        if len(data) < 16 or len(data) > cfg['MAX_FRAME_BYTES']:
            return
        seq, cts = struct.unpack('!Id', data[:12])
        jpeg, now = data[12:], time.time()
        rtm = self.runtime
        if not rtm.allow_frame(now, cfg['MAX_FRAME_RATE_HZ']) or self._frame_lock.locked():
            rtm.dropped += 1                     # back-pressure: never queue, never fall behind real time
            return
        async with self._frame_lock:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                executor(), partial(rtm.handle_frame, jpeg, seq=seq, client_ts=cts, arrival=now))
        self._frames_seen += 1
        if result.error:
            if result.error.startswith('extract_failed'):
                logger.warning('frame skipped: %s', result.error)
            return
        if result.signals:
            res = await database_sync_to_async(services.apply_signals)(self.session_id, result.signals, jpeg=jpeg, now=now)
            await self._announce(res)
        if now - self._last_status >= 2.0:
            self._last_status = now
            await self.send(text_data=json.dumps({'type': 'status', **result.summary}))

    async def proctor_text(self, data: dict) -> bool:
        """Returns True if the message was a proctoring message and has been handled."""
        t = data.get('type')
        if t == 'events':
            await self._on_events(data)
        elif t == 'heartbeat':
            await self._on_heartbeat()
        elif t == 'complete':
            await database_sync_to_async(services.complete_session)(self.session_id)
            await self.send(text_data=json.dumps({'type': 'completed'}))
            await self.close(code=1000)
        else:
            return False
        return True

    async def _on_events(self, data: dict):
        payload, seq, sig = data.get('payload'), data.get('seq'), data.get('sig')
        now = time.time()
        if not isinstance(payload, str) or not isinstance(seq, int) or len(payload) > MAX_PAYLOAD_BYTES:
            return
        key = tokens.derive_session_key(settings.PROCTOR['TOKEN_SECRET'], self.session_id)
        if not tokens.verify_batch(key, self.session_id, seq, payload, sig or ''):
            await self.proctor_event('transport', {'issue': 'bad_signature'}, 'server')
            return
        ok, expected = await database_sync_to_async(services.accept_telemetry_seq)(self.session_id, seq)
        if not ok:
            await self.proctor_event('transport', {'issue': 'seq_replay'}, 'server')
            return
        if seq > expected:
            await self.proctor_event('transport', {'issue': 'seq_gap', 'missing': seq - expected}, 'server')
        try:
            events = verify_events(json.loads(payload), now)
        except ValueError:
            return
        for e in events:
            await self.proctor_event(e['kind'], e['data'], 'client')

    async def _on_heartbeat(self):
        now = time.time()
        self.runtime.pipeline.process_event(Event('heartbeat', now, {}, 'client'))
        if now - self._last_hb_db >= 10:
            self._last_hb_db = now
            ua = next((v.decode() for k, v in self.scope.get('headers', []) if k == b'user-agent'), '')
            _, issues = await database_sync_to_async(services.heartbeat)(self.session_id, ip=self._client_ip(), user_agent=ua)
            for i in issues:
                await self.proctor_event('transport', i, 'server')

    async def proctor_event(self, kind: str, data: dict, origin: str = 'client'):
        """Feed one event through the pipeline and persist anything that counts."""
        signals = self.runtime.handle_event(kind, data, origin=origin)
        if signals:
            res = await database_sync_to_async(services.apply_signals)(
                self.session_id, signals, jpeg=self.runtime.last_jpeg)
            await self._announce(res)

    # -- outbound ---------------------------------------------------------------
    async def _announce(self, res: 'services.ApplyResult'):
        d = res.decision
        if d is None:
            return
        if res.terminated:
            await self.send(text_data=json.dumps({'type': 'terminated', 'message': d.message or
                                                  'This interview has ended.', 'strikes': d.strikes}))
            await self.close(code=4003)
            return
        if d.new_action and d.message:
            await self.send(text_data=json.dumps({
                'type': 'notice', 'level': Action(d.action).name.lower(), 'message': d.message,
                'strikes': d.strikes, 'max_strikes': services.policy_for(
                    await database_sync_to_async(ProctorSession.objects.get)(pk=self.session_id)).max_strikes}))

    async def _tick_loop(self):
        try:
            while True:
                await asyncio.sleep(TICK_INTERVAL_S)
                now = time.time()
                signals = self.runtime.tick(now)
                if signals:
                    res = await database_sync_to_async(services.apply_signals)(self.session_id, signals)
                    await self._announce(res)
                if now - self.runtime.last_state_save >= STATE_SAVE_INTERVAL_S:
                    self.runtime.last_state_save = now
                    await database_sync_to_async(services.save_runtime_state)(
                        self.session_id, self.runtime.pipeline.export_state(), self.runtime.frames, self.runtime.dropped)
        except asyncio.CancelledError:
            raise
        except Exception:                      # pragma: no cover
            logger.exception('proctor tick loop crashed')


class ProctorConsumer(ProctorMixin, AsyncWebsocketConsumer):
    """Dedicated proctoring socket: frames + telemetry only (no voice)."""

    async def connect(self):
        if not await self.proctor_connect():
            return
        subs = self.scope.get('subprotocols') or []
        await self.accept(subprotocol=SUBPROTOCOL if SUBPROTOCOL in subs else None)
        await self.proctor_ready_message()

    async def disconnect(self, code):
        await self.proctor_disconnect()

    async def receive(self, text_data=None, bytes_data=None):
        if self.session_id is None:
            return
        try:
            if bytes_data:
                await self.proctor_binary(bytes_data)
            elif text_data:
                if len(text_data) > settings.PROCTOR['MAX_WS_MESSAGE_BYTES']:
                    return
                data = json.loads(text_data)
                if not await self.proctor_text(data):
                    await self.send(text_data=json.dumps({'type': 'error', 'code': 'unknown_type'}))
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({'type': 'error', 'code': 'invalid_json'}))
