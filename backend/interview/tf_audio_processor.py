"""
TensorFlow-based audio processor with robust VAD and noise suppression.
Uses TensorFlow optimized math operations for fast, low-latency processing.
"""
import tensorflow as tf
import numpy as np
from collections import deque
from typing import Tuple


class TFAudioProcessor:
    """
    Handles audio normalization, noise suppression, and robust VAD.
    - Noise Suppression: Spectral subtraction for background noise reduction
    - Normalization: Dynamic gain control for consistent volume
    - Conversion: Float32 ↔ Int16 PCM
    - VAD: Multi-feature detection (energy + ZCR + spectral) with adaptive noise floor
    """
    
    def __init__(
        self, 
        target_sample_rate: int = 16000,  # Gemini uses 16kHz for input
        vad_energy_threshold: float = 0.025,  # Raised from 0.02 for stricter detection
        vad_zcr_threshold: float = 0.15,  # Zero-crossing rate threshold
        min_gain: float = 0.5,
        max_gain: float = 3.0,
        noise_alpha: float = 0.95  # Noise floor smoothing factor
    ):
        self.target_sample_rate = target_sample_rate
        self.vad_energy_threshold = vad_energy_threshold
        self.vad_zcr_threshold = vad_zcr_threshold
        self.min_gain = min_gain
        self.max_gain = max_gain
        self.noise_alpha = noise_alpha
        
        # State for robust VAD with hysteresis
        self._energy_history = deque(maxlen=150)  # Longer history for better noise estimation
        self._zcr_history = deque(maxlen=50)
        self._noise_floor_energy = 0.01
        self._noise_floor_zcr = 0.1
        self._is_speaking = False
        self._speech_frames = 0
        self._silence_frames = 0
        
        # Stricter hysteresis thresholds (frames) to avoid false positives
        self._speech_threshold = 5   # Frames of speech to confirm speaking (increased from 3)
        self._silence_threshold = 12  # Frames of silence to confirm stopped (increased from 10)
        
        # Noise suppression state
        self._noise_spectrum = None
        self._frame_count = 0
    
    def _spectral_noise_suppression(self, audio_tensor: tf.Tensor) -> tf.Tensor:
        """
        Apply spectral subtraction for noise suppression.
        
        Args:
            audio_tensor: Float32 audio tensor
            
        Returns:
            Noise-suppressed audio tensor
        """
        # Compute FFT
        fft = tf.signal.rfft(audio_tensor)
        magnitude = tf.abs(fft)
        phase = tf.math.angle(fft)
        
        # Estimate noise spectrum from first ~50 frames or quiet periods
        if self._noise_spectrum is None or self._frame_count < 50:
            # Initialize or update noise estimate during initial frames (likely background)
            if self._noise_spectrum is None:
                self._noise_spectrum = magnitude.numpy()
            else:
                # Exponential moving average for noise estimation
                self._noise_spectrum = (
                    self.noise_alpha * self._noise_spectrum + 
                    (1 - self.noise_alpha) * magnitude.numpy()
                )
            self._frame_count += 1
        
        # Spectral subtraction: subtract noise spectrum
        noise_tensor = tf.constant(self._noise_spectrum, dtype=tf.float32)
        suppressed_magnitude = tf.maximum(magnitude - 1.5 * noise_tensor, magnitude * 0.1)  # Over-subtraction factor 1.5
        
        # Reconstruct signal
        suppressed_fft = tf.cast(suppressed_magnitude, tf.complex64) * tf.exp(tf.complex(0.0, phase))
        suppressed_audio = tf.signal.irfft(suppressed_fft)
        
        # Ensure same length as input
        original_length = tf.shape(audio_tensor)[0]
        return suppressed_audio[:original_length]
    
    def _compute_zero_crossing_rate(self, audio_tensor: tf.Tensor) -> float:
        """
        Compute zero-crossing rate (ZCR) - indicator of speech vs noise.
        Speech typically has moderate ZCR, noise has high or low ZCR.
        
        Args:
            audio_tensor: Float32 audio tensor
            
        Returns:
            Zero-crossing rate (0.0 to 1.0)
        """
        # Count sign changes
        signs = tf.sign(audio_tensor)
        sign_changes = tf.abs(signs[1:] - signs[:-1])
        zcr = tf.reduce_sum(sign_changes) / (2.0 * tf.cast(tf.size(audio_tensor), tf.float32))
        return float(zcr)
    
    def _compute_spectral_centroid(self, audio_tensor: tf.Tensor) -> float:
        """
        Compute spectral centroid - indicator of brightness/frequency content.
        Speech has characteristic centroid range, noise often different.
        
        Args:
            audio_tensor: Float32 audio tensor
            
        Returns:
            Normalized spectral centroid
        """
        fft = tf.signal.rfft(audio_tensor)
        magnitude = tf.abs(fft)
        
        freqs = tf.range(0, tf.shape(magnitude)[0], dtype=tf.float32)
        centroid = tf.reduce_sum(freqs * magnitude) / (tf.reduce_sum(magnitude) + 1e-8)
        
        # Normalize by Nyquist frequency
        normalized_centroid = centroid / (tf.cast(tf.shape(magnitude)[0], tf.float32))
        return float(normalized_centroid)
    
    def process_audio(self, audio_data: bytes, input_format: str = 'float32') -> Tuple[bytes, bool]:
        """
        Process raw audio bytes with noise suppression, normalization, and robust VAD.
        
        Args:
            audio_data: Raw audio bytes
            input_format: 'float32' or 'int16'
            
        Returns:
            Tuple of (processed PCM16 bytes, is_speech boolean)
        """
        # 1. Convert bytes to Float32 Tensor
        if input_format == 'float32':
            audio_array = np.frombuffer(audio_data, dtype=np.float32)
        elif input_format == 'int16':
            audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            audio_array = np.frombuffer(audio_data, dtype=np.float32)
        
        if len(audio_array) == 0:
            return b'', False
            
        tensor = tf.constant(audio_array, dtype=tf.float32)
        
        # 2. Apply Spectral Noise Suppression (reduces background noise)
        try:
            tensor = self._spectral_noise_suppression(tensor)
        except Exception:
            # If noise suppression fails, continue with original
            pass
        
        # 3. Normalize Volume (Dynamic Gain Control)
        peak = float(tf.reduce_max(tf.abs(tensor)))
        if peak > 0.001:  # Avoid division by very small values
            target_gain = 0.7 / peak
            gain = np.clip(target_gain, self.min_gain, self.max_gain)
            tensor = tensor * gain
            tensor = tf.clip_by_value(tensor, -1.0, 1.0)
        
        # 4. Multi-Feature VAD (Voice Activity Detection)
        
        # Feature 1: Energy (RMS)
        energy = float(tf.sqrt(tf.reduce_mean(tf.square(tensor))))
        self._energy_history.append(energy)
        
        # Feature 2: Zero-Crossing Rate
        zcr = self._compute_zero_crossing_rate(tensor)
        self._zcr_history.append(zcr)
        
        # Feature 3: Spectral Centroid (frequency content)
        spectral_centroid = self._compute_spectral_centroid(tensor)
        
        # Update adaptive noise floors
        if len(self._energy_history) >= 30:
            sorted_energy = sorted(self._energy_history)
            # Use 30th percentile as noise floor (more aggressive)
            self._noise_floor_energy = sorted_energy[len(sorted_energy) * 30 // 100]
        
        if len(self._zcr_history) >= 20:
            sorted_zcr = sorted(self._zcr_history)
            self._noise_floor_zcr = sorted_zcr[len(sorted_zcr) // 4]
        
        # Multi-feature decision: ALL conditions must be met for speech
        energy_above_threshold = energy > max(self.vad_energy_threshold, self._noise_floor_energy * 3.0)  # 3x noise floor
        zcr_in_speech_range = (zcr > self.vad_zcr_threshold and zcr < 0.5)  # Speech has moderate ZCR
        spectral_in_speech_range = (spectral_centroid > 0.15 and spectral_centroid < 0.7)  # Speech frequency range
        
        # Require at least 2 of 3 features to indicate speech
        speech_features = sum([energy_above_threshold, zcr_in_speech_range, spectral_in_speech_range])
        is_frame_speech = speech_features >= 2
        
        # Hysteresis to prevent flickering from noise bursts
        if is_frame_speech:
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1
            self._speech_frames = 0
        
        # State machine for speaking detection (stricter thresholds)
        if self._speech_frames >= self._speech_threshold:
            self._is_speaking = True
        elif self._silence_frames >= self._silence_threshold:
            self._is_speaking = False
        
        # 5. Convert to Int16 PCM for Gemini
        pcm16 = (tensor.numpy() * 32767).astype(np.int16)
        
        return pcm16.tobytes(), self._is_speaking
    
    def convert_float32_to_pcm16(self, audio_data: bytes) -> bytes:
        """Convert Float32 audio to Int16 PCM."""
        audio_array = np.frombuffer(audio_data, dtype=np.float32)
        # Normalize if needed
        max_val = np.max(np.abs(audio_array))
        if max_val > 1.0:
            audio_array = audio_array / max_val
        pcm16 = (audio_array * 32767).astype(np.int16)
        return pcm16.tobytes()
    
    def convert_pcm16_to_float32(self, audio_data: bytes) -> bytes:
        """Convert Int16 PCM to Float32."""
        audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
        float32 = audio_array / 32768.0
        return float32.tobytes()
    
    def get_energy(self, audio_data: bytes, input_format: str = 'float32') -> float:
        """Get the energy level of audio data."""
        if input_format == 'float32':
            audio_array = np.frombuffer(audio_data, dtype=np.float32)
        else:
            audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        
        if len(audio_array) == 0:
            return 0.0
            
        tensor = tf.constant(audio_array, dtype=tf.float32)
        return float(tf.sqrt(tf.reduce_mean(tf.square(tensor))))
    
    def is_speaking(self) -> bool:
        """Get current speaking state."""
        return self._is_speaking
    
    def reset(self):
        """Reset VAD and noise suppression state."""
        self._energy_history.clear()
        self._zcr_history.clear()
        self._noise_floor_energy = 0.01
        self._noise_floor_zcr = 0.1
        self._is_speaking = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._noise_spectrum = None
        self._frame_count = 0


# Singleton instance for reuse
_processor_instance = None

def get_audio_processor() -> TFAudioProcessor:
    """Get or create the global audio processor instance."""
    global _processor_instance
    if _processor_instance is None:
        _processor_instance = TFAudioProcessor()
    return _processor_instance
