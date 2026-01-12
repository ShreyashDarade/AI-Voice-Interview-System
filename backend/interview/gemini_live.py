"""
Refactored Gemini Live API client for voice-to-voice interviews.
Based on proven test_gemini.py pattern with simplified WebSocket communication.
"""
import asyncio
import json
import base64
import logging
import websockets
from typing import Optional, Callable
from django.conf import settings

logger = logging.getLogger(__name__)


class GeminiLiveClient:
    """
    Production-ready Gemini Live API client for voice interviews.
    
    Key Features:
    - Simplified setup flow
    - Proper 16kHz input / 24kHz output audio handling
    - Turn completion tracking
    - Barge-in support via VAD
    - Resume context injection
    """
    
    def __init__(
        self,
        interview_id: str,
        resume_data: dict,
        experience_level: str,
        on_audio_response: Callable,
        on_text_response: Callable,
        on_error: Callable
    ):
        self.interview_id = interview_id
        self.resume_data = resume_data
        self.experience_level = experience_level
        
        # Callbacks
        self.on_audio_response = on_audio_response
        self.on_text_response = on_text_response
        self.on_error = on_error
        
        # WebSocket
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.ws_url = (
            f"wss://generativelanguage.googleapis.com/ws/"
            f"google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
            f"?key={settings.GEMINI_API_KEY}"
        )
        
        # State
        self.is_connected = False
        self.ai_is_speaking = False
        self.setup_complete = asyncio.Event()
    
    def _build_system_prompt(self) -> str:
        """
        Build context-aware system prompt for critical interviewer.
        
        Structured for implicit prompt caching:
        - Static instructions first (cached across sessions)
        - Variable resume data at end (unique per interview)
        """
        # Extract resume data
        skills = self.resume_data.get('skills', [])
        name = self.resume_data.get('name', 'candidate')
        skills_text = ', '.join(skills[:10]) if skills else 'Not specified'
        
        return f"""You are an expert AI Technical Interviewer conducting a professional voice interview. Your role is to thoroughly assess technical competency through critical evaluation.

=== CORE EVALUATION PRINCIPLES ===

CRITICAL EVALUATION STANDARDS:
1. DO NOT accept vague, generic, or incomplete answers
2. Question answers that lack technical depth or specificity
3. Ask "Why?", "How?", and "Can you elaborate?" frequently
4. Challenge incorrect assumptions politely but firmly
5. Request concrete examples and specific technical details
6. Verify understanding through counter-questions and follow-ups
7. If an answer is partially correct, probe for completeness
8. If an answer is incorrect, guide with hints: "Let's reconsider that approach..."

RESPONSE VALIDATION:
- Vague answer: "Can you be more specific about [technical detail]?"
- Incomplete answer: "That's a good start. What about [missing aspect]?"
- Incorrect answer: "Hmm, let's think about that differently. Consider [hint]..."
- Good answer: "Excellent! Let me dig deeper - [follow-up question]"
- Exceptional answer: "Great explanation! Moving on..."


=== INTERVIEW PROTOCOL ===

PHASE 1 - INTRODUCTION (MANDATORY):
First, you MUST introduce yourself extensively (speak for 30-45 seconds):
- "Hello! I'm your AI Technical Interviewer for today's session."
- Explain the interview structure:
  * Introduction phase where we get to know each other
  * Technical assessment covering their key skills
  * Opportunity for them to ask questions at the end
- Explain the format:
  * This is a voice-based interview, natural conversation style
  * They should think aloud and explain their reasoning
  * Honesty is valued - it's okay to say "I don't know" but try to reason through problems
  * Duration: approximately 20-30 minutes
- Set expectations:
  * You'll ask follow-up questions to understand depth
  * They should provide specific examples when possible

PHASE 2 - CANDIDATE INTRODUCTION (MANDATORY):
After YOUR introduction, request theirs:
- "Now, please introduce yourself. Tell me about your background, experience, and what you consider your key technical strengths."
- Listen to their complete introduction
- Ask 1-2 follow-up questions about their background:
  * "You mentioned [experience/project]. Can you tell me more about that?"
  * "What aspect of [technology] are you most passionate about?"

PHASE 3 - TECHNICAL ASSESSMENT:
Only after both introductions are complete:
- Ask ONE focused technical question at a time
- Wait for their COMPLETE answer before responding
- Evaluate each answer critically (see standards above)
- Ask counter-questions when answers are unclear or incomplete
- Keep YOUR responses brief (2-3 sentences) - listen more than you talk
- Adapt difficulty based on their experience level


=== QUESTIONING STRATEGY ===

QUESTION FLOW:
1. Start with fundamental concepts in their skill areas
2. If they answer well, increase difficulty progressively
3. If they struggle, provide hints and guide them
4. Always ask "Why?" or "How?" to verify understanding
5. Request real-world examples: "Have you used this in a project?"

COUNTER-QUESTIONING EXAMPLES:
- After explanation: "What would happen if [edge case]?"
- For design questions: "What are the trade-offs of that approach?"
- For algorithms: "What's the time complexity? Can we optimize?"
- For concepts: "How does that differ from [related concept]?"

KEEP IT CONVERSATIONAL:
- This is a VOICE interview - avoid listing multiple options
- Don't say: "Let me ask about A, B, C, and D"
- Instead: "Let's talk about [A]..." then follow up naturally
- Be encouraging but honest: "Good thinking" or "Let's explore that further"


=== CANDIDATE PROFILE ===
Experience Level: {self.experience_level}
Candidate Name: {name}
Key Skills to Assess: {skills_text}

Focus your technical questions on their listed skills, but verify genuine depth of knowledge through critical evaluation and follow-up questions. Adjust difficulty based on their experience level."""
    
    async def connect(self):
        """Establish WebSocket connection and setup."""
        logger.info(f"[Gemini] Connecting to: {self.ws_url[:50]}...")
        try:
            # Connect to Gemini Live API
            self.ws = await websockets.connect(
                self.ws_url,
                extra_headers={"Content-Type": "application/json"}
            )
            self.is_connected = True
            logger.info("[Gemini] WebSocket connected successfully")
            
            # Send setup with all configuration
            setup_message = {
                "setup": {
                    "model": f"models/{settings.GEMINI_MODEL}",
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {
                                    "voiceName": settings.GEMINI_VOICE_NAME
                                }
                            }
                        }
                    },
                    "systemInstruction": {
                        "parts": [{"text": self._build_system_prompt()}]
                    }
                }
            }
            
            logger.info(f"[Gemini] Sending setup with model: {settings.GEMINI_MODEL}, voice: {settings.GEMINI_VOICE_NAME}")
            await self.ws.send(json.dumps(setup_message))
            
            # Wait for setup confirmation
            logger.info("[Gemini] Waiting for setup confirmation...")
            response = await asyncio.wait_for(self.ws.recv(), timeout=10.0)
            data = json.loads(response)
            logger.info(f"[Gemini] Received setup response: {list(data.keys())}")
            
            if "setupComplete" in data:
                self.setup_complete.set()
                logger.info("[Gemini] Setup complete! Sending greeting trigger...")
                # Trigger AI to start with greeting
                await self._send_greeting_trigger()
            else:
                error_msg = f"Setup failed: {data}"
                logger.error(f"[Gemini] {error_msg}")
                raise ValueError(error_msg)
                
        except (websockets.exceptions.WebSocketException, asyncio.TimeoutError, ValueError) as e:
            logger.error(f"[Gemini] Connection error: {type(e).__name__}: {str(e)}")
            await self.on_error(f"Connection failed: {str(e)}")
            raise
    
    async def _send_greeting_trigger(self):
        """Send trigger for AI to start with full introduction protocol."""
        message = {
            "clientContent": {
                "turns": [{
                    "role": "user",
                    "parts": [{
                        "text": "[Begin the interview. Start with your extensive introduction as specified in PHASE 1, then request the candidate's introduction as specified in PHASE 2.]"
                    }]
                }],
                "turnComplete": True
            }
        }
        await self.ws.send(json.dumps(message))
    
    async def send_audio(self, audio_data: bytes):
        """
        Send audio to Gemini.
        
        Args:
            audio_data: Raw PCM16 audio bytes (16kHz, mono)
        """
        if not self.is_connected or not self.ws:
            logger.warning("[Gemini] Cannot send audio: not connected")
            return
        
        # Only send if AI is not speaking (prevent echo)
        if self.ai_is_speaking:
            return
        
        try:
            message = {
                "realtimeInput": {
                    "mediaChunks": [{
                        "data": base64.b64encode(audio_data).decode(),
                        "mimeType": "audio/pcm"
                    }]
                }
            }
            await self.ws.send(json.dumps(message))
        except Exception as e:
            logger.error(f"[Gemini] Error sending audio: {e}")
            await self.on_error(f"Error sending audio: {str(e)}")
    
    async def send_text(self, text: str):
        """Send text message (for system commands or barge-in)."""
        if not self.is_connected or not self.ws:
            return
        
        try:
            message = {
                "clientContent": {
                    "turns": [{
                        "role": "user",
                        "parts": [{"text": text}]
                    }],
                    "turnComplete": True
                }
            }
            await self.ws.send(json.dumps(message))
        except Exception as e:
            await self.on_error(f"Error sending text: {str(e)}")
    
    async def send_turn_complete(self):
        """Signal that user has finished their turn (stopped speaking)."""
        if not self.is_connected or not self.ws:
            return
        
        try:
            # Send empty turn with turnComplete=True to signal end
            message = {
                "clientContent": {
                    "turnComplete": True
                }
            }
            await self.ws.send(json.dumps(message))
            logger.info("[Gemini] Sent turn complete signal")
        except Exception as e:
            logger.error(f"[Gemini] Error sending turn complete: {e}")
    
    async def receive_loop(self):
        """Continuously receive and process messages from Gemini."""
        if not self.ws:
            logger.error("[Gemini] receive_loop called with no WebSocket")
            return
        
        logger.info("[Gemini] Starting receive loop...")
        try:
            async for message in self.ws:
                data = json.loads(message)
                logger.debug(f"[Gemini] Received message: {list(data.keys())}")
                await self._handle_message(data)
                
        except websockets.exceptions.ConnectionClosed as e:
            self.is_connected = False
            logger.error(f"[Gemini] Connection closed: code={e.code}, reason={e.reason}")
            await self.on_error(f"Connection closed: {e}")
        except Exception as e:
            logger.error(f"[Gemini] Receive error: {type(e).__name__}: {e}")
            await self.on_error(f"Receive error: {str(e)}")
    
    async def _handle_message(self, data: dict):
        """Process incoming messages from Gemini."""
        # Check for setup completion
        if "setupComplete" in data:
            logger.info("[Gemini] Setup completion confirmed in message")
            self.setup_complete.set()
            return
        
        # Handle server content (AI responses)
        if "serverContent" in data:
            logger.debug("[Gemini] Processing server content")
            await self._handle_server_content(data["serverContent"])
    
    async def _handle_server_content(self, content: dict):
        """Handle server content responses (audio/text)."""
        # Extract audio from modelTurn
        if "modelTurn" in content:
            self.ai_is_speaking = True
            await self._process_model_turn(content["modelTurn"])
        
        # Check for turn completion
        if content.get("turnComplete", False):
            self.ai_is_speaking = False
    
    async def _process_model_turn(self, model_turn: dict):
        """Process parts from model turn."""
        for part in model_turn.get("parts", []):
            # Handle audio response
            if "inlineData" in part:
                audio_b64 = part["inlineData"]["data"]
                audio_bytes = base64.b64decode(audio_b64)
                await self.on_audio_response(audio_bytes)
            
            # Handle text response (for transcript)
            if "text" in part:
                await self.on_text_response(part["text"])
    
    async def close(self):
        """Close the WebSocket connection."""
        logger.info("[Gemini] Closing connection")
        self.is_connected = False
        if self.ws:
            await self.ws.close()
            logger.info("[Gemini] Connection closed")
