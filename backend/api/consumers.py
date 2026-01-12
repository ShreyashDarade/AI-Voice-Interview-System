"""
WebSocket consumers for real-time interview communication.
"""
import json
import asyncio
import base64
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.conf import settings

from interview.gemini_live import GeminiLiveClient
from interview.tf_audio_processor import TFAudioProcessor

logger = logging.getLogger(__name__)


class InterviewConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for interview sessions.
    Bridges client audio to Gemini Live API for voice interviews.
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.interview_id = None
        self.interview = None
        self.gemini_client = None
        self.receive_task = None
        # Create audio processor with configured settings
        self.audio_processor = TFAudioProcessor(
            vad_energy_threshold=settings.VAD_ENERGY_THRESHOLD,
            vad_zcr_threshold=settings.VAD_ZCR_THRESHOLD
        )
        self.audio_processor._speech_threshold = settings.VAD_SPEECH_FRAMES
        self.audio_processor._silence_threshold = settings.VAD_SILENCE_FRAMES
        self._audio_frame_count = 0
        self._speech_detected_count = 0
        # Turn management to prevent AI interruption
        self._user_is_speaking = False
        self._silence_frame_count = 0
        self._turn_sent_audio = False
        self._last_speech_time = 0
    
    async def connect(self):
        """Handle WebSocket connection."""
        self.interview_id = self.scope['url_route']['kwargs']['interview_id']
        logger.info(f"[Consumer] WebSocket connection request for interview: {self.interview_id}")
        
        # Validate interview exists and is in progress
        self.interview = await self.get_interview()
        
        if not self.interview:
            logger.error(f"[Consumer] Interview not found: {self.interview_id}")
            await self.close(code=4004)
            return
        
        if self.interview.status != 'in_progress':
            logger.error(f"[Consumer] Interview not in progress: {self.interview.status}")
            await self.close(code=4001)
            return
        
        await self.accept()
        logger.info(f"[Consumer] WebSocket accepted for interview: {self.interview_id}")
        logger.info(f"[Consumer] VAD configured - Energy: {settings.VAD_ENERGY_THRESHOLD}, ZCR: {settings.VAD_ZCR_THRESHOLD}, Noise Suppression: {settings.NOISE_SUPPRESSION_ENABLED}")
        
        # Initialize Gemini Live client
        try:
            logger.info("[Consumer] Initializing Gemini client...")
            self.gemini_client = GeminiLiveClient(
                interview_id=self.interview_id,
                resume_data=self.interview.resume.parsed_data,
                experience_level=self.interview.experience_level,
                on_audio_response=self.send_audio_to_client,
                on_text_response=self.send_text_to_client,
                on_error=self.handle_gemini_error
            )
            
            logger.info("[Consumer] Connecting to Gemini...")
            await self.gemini_client.connect()
            
            # Start receiving from Gemini in background
            self.receive_task = asyncio.create_task(self.gemini_client.receive_loop())
            logger.info("[Consumer] Gemini receive loop started")
            
            # Send connection success
            await self.send(text_data=json.dumps({
                'type': 'connected',
                'message': 'Interview session started',
                'interview_id': self.interview_id
            }))
            logger.info("[Consumer] Sent connection success to client")
            
        except Exception as e:
            logger.error(f"[Consumer] Failed to connect to Gemini: {type(e).__name__}: {str(e)}")
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Failed to connect to AI: {str(e)}'
            }))
            await self.close(code=4002)
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        logger.info(f"[Consumer] WebSocket disconnect requested, code: {close_code}")
        logger.info(f"[Consumer] Audio stats - Total frames: {self._audio_frame_count}, Speech detected: {self._speech_detected_count}, Filter rate: {((self._audio_frame_count - self._speech_detected_count) / max(self._audio_frame_count, 1) * 100):.1f}%")
        # Cancel receive task
        if self.receive_task:
            self.receive_task.cancel()
            try:
                await self.receive_task
            except asyncio.CancelledError:
                # Expected when task is cancelled - cleanup then re-raise
                await self._cleanup_connection()
                raise  # Re-raise CancelledError as required by asyncio
        
        await self._cleanup_connection()
    
    async def _cleanup_connection(self):
        """Clean up resources on disconnect."""
        # Close Gemini connection
        if self.gemini_client:
            await self.gemini_client.close()
        
        # Update interview if still in progress
        if self.interview and self.interview.status == 'in_progress':
            await self.end_interview()
    
    async def receive(self, text_data=None, bytes_data=None):
        """Handle incoming WebSocket messages."""
        try:
            if bytes_data:
                await self._handle_binary_audio(bytes_data)
            elif text_data:
                await self._handle_text_message(text_data)
        except json.JSONDecodeError:
            await self._send_error('Invalid JSON')
        except Exception as e:
            await self._send_error(str(e))
    
    async def _handle_binary_audio(self, bytes_data: bytes):
        """Handle binary audio data from client with turn-based VAD (prevents AI interruption)."""
        if not self.gemini_client:
            return
        
        self._audio_frame_count += 1
        import time
        current_time = time.time()
            
        # Process audio with TF processor (noise suppression + normalization + VAD)
        try:
            pcm16_data, is_speech = self.audio_processor.process_audio(
                bytes_data, 
                input_format='float32'
            )
            
            # Debug logging every 100 frames
            if self._audio_frame_count % 100 == 0:
                energy = self.audio_processor.get_energy(bytes_data)
                logger.debug(f"[VAD] Frame {self._audio_frame_count}: energy={energy:.4f}, noise_floor={self.audio_processor._noise_floor_energy:.4f}, is_speech={is_speech}, user_speaking={self._user_is_speaking}")
            
            # TURN MANAGEMENT: Process based on speech detection
            await self._process_turn_management(is_speech, pcm16_data, current_time)
                
        except Exception as e:
            logger.warning(f"[VAD] Audio processing failed: {e}, using fallback")
            # Fallback: send raw audio without processing
            await self.gemini_client.send_audio(bytes_data)
    
    async def _process_turn_management(self, is_speech: bool, pcm16_data: bytes, current_time: float):
        """Process turn management based on speech detection."""
        if is_speech:
            await self._handle_speech_detected(pcm16_data, current_time)
        else:
            await self._handle_silence_detected()
    
    async def _handle_speech_detected(self, pcm16_data: bytes, current_time: float):
        """Handle when speech is detected."""
        # User is speaking
        if not self._user_is_speaking:
            # User started speaking - begin new turn
            self._user_is_speaking = True
            self._turn_sent_audio = False
            logger.info("[TURN] User started speaking")
            
            # If AI is speaking, send barge-in signal ONCE
            if self.gemini_client.ai_is_speaking:
                logger.info("[TURN] User barge-in detected - interrupting AI")
                await self.gemini_client.send_text("[User interrupted]")
        
        # Reset silence counter
        self._silence_frame_count = 0
        self._last_speech_time = current_time
        
        # Send audio during user's turn
        if len(pcm16_data) > 0:
            self._speech_detected_count += 1
            self._turn_sent_audio = True
            await self.gemini_client.send_audio(pcm16_data)
    
    async def _handle_silence_detected(self):
        """Handle when silence is detected."""
        # No speech detected
        if self._user_is_speaking:
            # User was speaking - count silence frames
            self._silence_frame_count += 1
            
            # If silence exceeds threshold (e.g., 30 frames ~3 seconds at 100ms/frame)
            # User has finished their turn
            if self._silence_frame_count >= 30:
                if self._turn_sent_audio:
                    # Complete the turn - signal to Gemini
                    logger.info(f"[TURN] User finished speaking (silence: {self._silence_frame_count} frames)")
                    await self.gemini_client.send_turn_complete()
                
                # Reset turn state
                self._user_is_speaking = False
                self._silence_frame_count = 0
                self._turn_sent_audio = False
    
    async def _handle_text_message(self, text_data: str):
        """Handle text-based WebSocket messages."""
        data = json.loads(text_data)
        message_type = data.get('type')
        
        handlers = {
            'audio': lambda: self._handle_base64_audio(data),
            'text': lambda: self._handle_text_input(data),
            'cheating_detected': lambda: self.handle_cheating_event(data),
            'end_interview': self._handle_end_request,
        }
        
        handler = handlers.get(message_type)
        if handler:
            await handler()
    
    async def _handle_base64_audio(self, data: dict):
        """Handle base64-encoded audio."""
        audio_bytes = base64.b64decode(data['data'])
        if self.gemini_client:
            await self.gemini_client.send_audio(audio_bytes)
    
    async def _handle_text_input(self, data: dict):
        """Handle text input for testing."""
        if self.gemini_client:
            await self.gemini_client.send_text(data['text'])
    
    async def _handle_end_request(self):
        """Handle interview end request."""
        await self.end_interview()
        await self.close()
    
    async def _send_error(self, message: str):
        """Send error message to client."""
        await self.send(text_data=json.dumps({'type': 'error', 'message': message}))
    
    async def send_audio_to_client(self, audio_data: bytes):
        """Send audio response to client (24kHz PCM from Gemini)."""
        await self.send(bytes_data=audio_data)
    
    async def send_text_to_client(self, text: str):
        """Send text response to client."""
        await self.send(text_data=json.dumps({
            'type': 'transcript',
            'text': text
        }))
    
    async def handle_gemini_error(self, error: str):
        """Handle errors from Gemini."""
        await self.send(text_data=json.dumps({
            'type': 'error',
            'message': error
        }))
    
    async def handle_cheating_event(self, data: dict):
        """Handle cheating detection event."""
        confidence = data.get('confidence', 0.0)
        
        if confidence < settings.CHEATING_CONFIDENCE_THRESHOLD:
            return
        
        # Add strike
        strikes = await self.add_strike()
        max_strikes = settings.MAX_STRIKES
        
        # Send warning
        if strikes >= max_strikes:
            await self.send(text_data=json.dumps({
                'type': 'terminated',
                'message': 'Interview terminated due to suspected cheating.',
                'strikes': strikes
            }))
            await self.terminate_interview()
            await self.close(code=4003)
        else:
            warning = self._get_warning_message(strikes)
            await self.send(text_data=json.dumps({
                'type': 'warning',
                'message': warning,
                'strikes': strikes,
                'max_strikes': max_strikes
            }))
            
            # Have AI acknowledge the warning
            if self.gemini_client:
                await self.gemini_client.send_text(
                    f"[System: Warning issued to candidate. Strike {strikes} of {max_strikes}]"
                )
    
    
    def _get_warning_message(self, strikes: int) -> str:
        """Generate warning message based on strike count."""
        if strikes == 1:
            return "STRIKE 1/2: Suspicious activity detected. One more violation will terminate your interview."
        elif strikes == 2:
            return "FINAL STRIKE: Interview will be terminated immediately on next violation."
        return "Interview terminated."
    
    @database_sync_to_async
    def get_interview(self):
        """Get interview from database."""
        from core.models import Interview
        try:
            return Interview.objects.select_related('resume').get(id=self.interview_id)
        except Interview.DoesNotExist:
            return None
    
    @database_sync_to_async
    def add_strike(self):
        """Add a strike to the interview."""
        from core.models import Interview
        interview = Interview.objects.get(id=self.interview_id)
        interview.strikes += 1
        interview.save()
        return interview.strikes
    
    @database_sync_to_async
    def end_interview(self):
        """End the interview."""
        from core.models import Interview
        interview = Interview.objects.get(id=self.interview_id)
        if interview.status == 'in_progress':
            interview.end()
    
    @database_sync_to_async
    def terminate_interview(self):
        """Terminate the interview due to cheating."""
        from core.models import Interview
        interview = Interview.objects.get(id=self.interview_id)
        interview.end(
            terminated=True,
            reason=f"Terminated after {settings.MAX_STRIKES} cheating violations."
        )
