"""
NumPy audio front-end for the voice interview: spectral-gate noise
suppression, gain control and a multi-feature VAD with hysteresis.

(Replaces the former TensorFlow implementation. TensorFlow was only used for
``rfft`` and element-wise math, which NumPy does identically at a fraction of
the install size and startup time.)
"""
from __future__ import annotations

from collections import deque
from typing import Tuple

import numpy as np


class AudioProcessor:
    def __init__(
        self,
        target_sample_rate: int = 16000,
        vad_energy_threshold: float = 0.025,
        vad_zcr_threshold: float = 0.15,
        min_gain: float = 0.5,
        max_gain: float = 3.0,
        noise_alpha: float = 0.95,
        speech_frames: int = 5,
        silence_frames: int = 12,
    ):
        self.target_sample_rate = target_sample_rate
        self.vad_energy_threshold = vad_energy_threshold
        self.vad_zcr_threshold = vad_zcr_threshold
        self.min_gain, self.max_gain = min_gain, max_gain
        self.noise_alpha = noise_alpha

        self._energy_history: deque = deque(maxlen=150)
        self._zcr_history: deque = deque(maxlen=50)
        self._noise_floor_energy = 0.01
        self._noise_floor_zcr = 0.1
        self._is_speaking = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_threshold = speech_frames
        self._silence_threshold = silence_frames
        self._noise_spectrum: np.ndarray | None = None
        self._frame_count = 0

    # -- DSP ------------------------------------------------------------------
    def _spectral_noise_suppression(self, x: np.ndarray) -> np.ndarray:
        spec = np.fft.rfft(x)
        mag, phase = np.abs(spec), np.angle(spec)
        if self._noise_spectrum is not None and self._noise_spectrum.shape != mag.shape:
            self._noise_spectrum, self._frame_count = None, 0         # chunk size changed: relearn
        if self._noise_spectrum is None:
            self._noise_spectrum = mag.copy()
            self._frame_count += 1
        elif self._frame_count < 50:                                  # learn noise from the first frames
            self._noise_spectrum = self.noise_alpha * self._noise_spectrum + (1 - self.noise_alpha) * mag
            self._frame_count += 1
        cleaned = np.maximum(mag - 1.5 * self._noise_spectrum, mag * 0.1)
        out = np.fft.irfft(cleaned * np.exp(1j * phase), n=len(x))
        return out.astype(np.float32)

    @staticmethod
    def _zcr(x: np.ndarray) -> float:
        if x.size < 2:
            return 0.0
        return float(np.abs(np.diff(np.sign(x))).sum() / (2.0 * x.size))

    @staticmethod
    def _spectral_centroid(x: np.ndarray) -> float:
        mag = np.abs(np.fft.rfft(x))
        if mag.size == 0:
            return 0.0
        centroid = float((np.arange(mag.size) * mag).sum() / (mag.sum() + 1e-8))
        return centroid / mag.size

    @staticmethod
    def _decode(audio_data: bytes, input_format: str) -> np.ndarray:
        if input_format == 'int16':
            n = len(audio_data) // 2
            return np.frombuffer(audio_data[: n * 2], dtype=np.int16).astype(np.float32) / 32768.0
        n = len(audio_data) // 4
        a = np.frombuffer(audio_data[: n * 4], dtype=np.float32)
        return np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)

    # -- public ---------------------------------------------------------------
    def process_audio(self, audio_data: bytes, input_format: str = 'float32') -> Tuple[bytes, bool]:
        x = self._decode(audio_data, input_format)
        if x.size == 0:
            return b'', False
        try:
            x = self._spectral_noise_suppression(x)
        except Exception:
            pass
        peak = float(np.abs(x).max())
        if peak > 0.001:
            x = np.clip(x * float(np.clip(0.7 / peak, self.min_gain, self.max_gain)), -1.0, 1.0)

        energy = float(np.sqrt(np.mean(np.square(x))))
        zcr = self._zcr(x)
        centroid = self._spectral_centroid(x)
        self._energy_history.append(energy)
        self._zcr_history.append(zcr)
        if len(self._energy_history) >= 30:
            s = sorted(self._energy_history)
            self._noise_floor_energy = s[len(s) * 30 // 100]
        if len(self._zcr_history) >= 20:
            s = sorted(self._zcr_history)
            self._noise_floor_zcr = s[len(s) // 4]

        # Energy is a hard gate (the original 2-of-3 vote let broadband noise at near-zero
        # energy pass on ZCR + centroid alone); ZCR / spectral shape then confirm it is voice-like.
        loud_enough = energy > max(self.vad_energy_threshold, self._noise_floor_energy * 3.0)
        shape_votes = sum([self.vad_zcr_threshold < zcr < 0.5, 0.15 < centroid < 0.7])
        if loud_enough and shape_votes >= 1:
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1
            self._speech_frames = 0
        if self._speech_frames >= self._speech_threshold:
            self._is_speaking = True
        elif self._silence_frames >= self._silence_threshold:
            self._is_speaking = False
        return (x * 32767).astype(np.int16).tobytes(), self._is_speaking

    def get_energy(self, audio_data: bytes, input_format: str = 'float32') -> float:
        x = self._decode(audio_data, input_format)
        return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0

    @property
    def noise_floor(self) -> float:
        return self._noise_floor_energy

    def is_speaking(self) -> bool:
        return self._is_speaking

    def reset(self) -> None:
        self.__init__(self.target_sample_rate, self.vad_energy_threshold, self.vad_zcr_threshold,
                      self.min_gain, self.max_gain, self.noise_alpha, self._speech_threshold, self._silence_threshold)
