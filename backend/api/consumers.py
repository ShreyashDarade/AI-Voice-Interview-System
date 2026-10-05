"""
Voice-interview WebSocket.

Authentication, proctoring frames/telemetry and escalation come from
``ProctorMixin`` (so the interview socket and the standalone proctoring socket
behave identically). This class adds the Gemini voice bridge and feeds the
proctoring pipeline the audio-side signals it needs (voice activity and answer
latency) -- strictly server-side, never client-reported.

Client -> server
  binary  : raw float32 mic audio (16 kHz)              (legacy, unchanged)
            b'PXF1' + 12-byte header + JPEG              (webcam frame, see proctoring.consumers)
  text    : {"type":"events"|"heartbeat"|"complete", ...}   proctoring messages
            {"type":"text","text":...}                     debug text input
            {"type":"end_interview"}                        alias of "complete"
Server -> client
  binary  : 24 kHz PCM16 interviewer audio
  text    : ready / status / notice / terminated / transcript / error
"""
import asyncio
import base64
import json
import logging
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from interview.audio_processor import AudioProcessor
from interview.gemini_live import GeminiLiveClient
from proctoring.consumers import SUBPROTOCOL, ProctorMixin
from proctoring.models import ProctorSession

logger = logging.getLogger(__name__)

FRAME_MAGIC = b'PXF1'
SILENCE_FRAMES_END_OF_TURN = 30      # ~3 s of silence at 100 ms frames
MAX_TRANSCRIPT_CHARS = 200_000


class InterviewConsumer(ProctorMixin, AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gemini_client = None
        self.receive_task = None
        self.audio = AudioProcessor(
            vad_energy_threshold=settings.VAD_ENERGY_THRESHOLD, vad_zcr_threshold=settings.VAD_ZCR_THRESHOLD,
            speech_frames=settings.VAD_SPEECH_FRAMES, silence_frames=settings.VAD_SILENCE_FRAMES)
        self._audio_frames = 0
        self._user_is_speaking = False
        self._silence_frames = 0
        self._turn_sent_audio = False
        self._transcript: list[dict] = []
        self._transcript_chars = 0

    # -- lifecycle ----------------------------------------------------------------
    async def connect(self):
        if not await self.proctor_connect():
            return
        state = await database_sync_to_async(lambda: ProctorSession.objects.values_list('state', flat=True).get(pk=self.session_id))()
        if state != ProctorSession.State.ACTIVE:
            await self.proctor_disconnect()
            await self.close(code=4012)              # session not started yet
            return
        subs = self.scope.get('subprotocols') or []
        await self.accept(subprotocol=SUBPROTOCOL if SUBPROTOCOL in subs else None)
        await self.proctor_ready_message()
        try:
            await self._start_gemini()
        except Exception as exc:                      # noqa: BLE001
            logger.error('[Consumer] Gemini start failed: %s', type(exc).__name__)
            await self.send(text_data=json.dumps({'type': 'error', 'code': 'ai_unavailable',
                                                  'message': 'Could not connect to the AI interviewer.'}))
            await self.close(code=4002)

    async def _start_gemini(self):
        interview = await database_sync_to_async(self._load_interview)()
        resume = dict(interview.resume.parsed_data or {})
        resume.setdefault('name', interview.resume.candidate_name)
        resume.setdefault('skills', interview.resume.skills)
        self.gemini_client = GeminiLiveClient(
            interview_id=str(interview.id), resume_data=resume, experience_level=interview.experience_level,
            on_audio_response=self.send_audio_to_client, on_text_response=self.send_text_to_client,
            on_error=self.handle_gemini_error, on_turn_complete=self._on_ai_turn_complete,
            on_transcript=self._on_transcript,
            resumption_handle=(interview.session_data or {}).get('gemini_resumption_handle'))
        await self.gemini_client.connect()
        self.receive_task = asyncio.create_task(self.gemini_client.receive_loop())
        await self.send(text_data=json.dumps({'type': 'connected', 'interview_id': str(interview.id)}))

    def _load_interview(self):
        s = ProctorSession.objects.select_related('interview__resume').get(pk=self.session_id)
        return s.interview

    async def disconnect(self, code):
        if self.receive_task:
            self.receive_task.cancel()
            try:
                await self.receive_task
            except (asyncio.CancelledError, Exception):      # noqa: BLE001
                pass
        if self.gemini_client:
            handle = self.gemini_client.resumption_handle
            await self.gemini_client.close()
            await database_sync_to_async(self._persist_session_data)(handle)
        await self.proctor_disconnect()       # saves detector state, marks session PAUSED (resumable)

    def _persist_session_data(self, handle):
        from core.models import Interview
        s = ProctorSession.objects.filter(pk=self.session_id).select_related('interview').first()
        if not s:
            return
        i = s.interview
        data = dict(i.session_data or {})
        if handle:
            data['gemini_resumption_handle'] = handle
        if self._transcript:
            data['transcript'] = (data.get('transcript', []) + self._transcript)[-2000:]
            self._transcript = []
        i.session_data = data
        i.save(update_fields=['session_data', 'updated_at'])

    # -- inbound ----------------------------------------------------------------
    async def receive(self, text_data=None, bytes_data=None):
        if self.session_id is None:
            return
        try:
            if bytes_data:
                if bytes_data.startswith(FRAME_MAGIC):
                    await self.proctor_binary(bytes_data[len(FRAME_MAGIC):])
                else:
                    await self._handle_binary_audio(bytes_data)
            elif text_data:
                if len(text_data) > settings.PROCTOR['MAX_WS_MESSAGE_BYTES']:
                    return
                data = json.loads(text_data)
                if data.get('type') == 'end_interview':
                    data = {'type': 'complete'}
                if await self.proctor_text(data):
                    return
                if data.get('type') == 'audio':
                    await self._send_to_gemini(base64.b64decode(data['data']))
                elif data.get('type') == 'text' and self.gemini_client:
                    await self.gemini_client.send_text(str(data.get('text', ''))[:2000])
        except (json.JSONDecodeError, KeyError, ValueError):
            await self.send(text_data=json.dumps({'type': 'error', 'code': 'bad_message'}))
        except Exception:                                      # noqa: BLE001
            logger.exception('[Consumer] receive failed')
            await self.send(text_data=json.dumps({'type': 'error', 'code': 'internal'}))

    async def _send_to_gemini(self, pcm: bytes):
        if self.gemini_client and len(pcm) <= settings.MAX_AUDIO_CHUNK_SIZE_KB * 1024:
            await self.gemini_client.send_audio(pcm)

    async def _handle_binary_audio(self, data: bytes):
        if not self.gemini_client or len(data) > settings.MAX_AUDIO_CHUNK_SIZE_KB * 1024:
            return
        self._audio_frames += 1
        try:
            pcm16, is_speech = self.audio.process_audio(data, input_format='float32')
        except Exception:                                      # noqa: BLE001
            logger.warning('[VAD] processing failed; dropping chunk')
            return
        # Ignore the mic while the interviewer speaks: it is echo, not the candidate.
        if self.gemini_client.ai_is_speaking:
            return
        if is_speech:
            await self._on_speech(pcm16)
        else:
            await self._on_silence()

    async def _on_speech(self, pcm16: bytes):
        if not self._user_is_speaking:
            self._user_is_speaking, self._turn_sent_audio = True, False
            now = time.time()
            await self.proctor_event('vad', {'speech': True}, 'server')
            await self.proctor_event('turn', {'phase': 'user_start'}, 'server')
        self._silence_frames = 0
        if pcm16:
            self._turn_sent_audio = True
            await self.gemini_client.send_audio(pcm16)

    async def _on_silence(self):
        if not self._user_is_speaking:
            return
        self._silence_frames += 1
        if self._silence_frames >= SILENCE_FRAMES_END_OF_TURN:
            if self._turn_sent_audio:
                await self.gemini_client.send_turn_complete()
            self._user_is_speaking, self._silence_frames, self._turn_sent_audio = False, 0, False
            await self.proctor_event('vad', {'speech': False}, 'server')

    # -- outbound ---------------------------------------------------------------
    async def send_audio_to_client(self, audio: bytes):
        await self.send(bytes_data=audio)

    async def send_text_to_client(self, text: str):
        await self.send(text_data=json.dumps({'type': 'transcript', 'role': 'interviewer', 'text': text}))

    async def handle_gemini_error(self, message: str):
        await self.send(text_data=json.dumps({'type': 'error', 'code': 'ai', 'message': message}))

    async def _on_ai_turn_complete(self):
        await self.proctor_event('turn', {'phase': 'ai_end'}, 'server')

    async def _on_transcript(self, role: str, text: str):
        if self._transcript_chars < MAX_TRANSCRIPT_CHARS:
            self._transcript.append({'role': role, 'text': text, 'ts': time.time()})
            self._transcript_chars += len(text)
        await self.send(text_data=json.dumps({'type': 'transcript', 'role': role, 'text': text}))
