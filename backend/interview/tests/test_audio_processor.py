import numpy as np

from interview.audio_processor import AudioProcessor


def loud(n=1600, seed=0):
    x = np.convolve(np.random.default_rng(seed).normal(0, 0.4, n + 1), [0.5, 0.5], mode='valid')
    return np.clip(x, -1, 1).astype(np.float32).tobytes()


def quiet(n=1600, seed=1):
    return np.random.default_rng(seed).normal(0, 0.0005, n).astype(np.float32).tobytes()


def test_silence_is_never_speech_even_if_broadband():
    p = AudioProcessor()
    assert not any(p.process_audio(quiet(seed=i))[1] for i in range(80))


def test_speech_needs_consecutive_frames_then_releases_with_hysteresis():
    p = AudioProcessor()
    for i in range(60):
        p.process_audio(quiet(seed=i))
    states = [p.process_audio(loud(seed=i))[1] for i in range(15)]
    assert states[:4] == [False] * 4 and all(states[5:])                 # 5 frames to confirm
    tail = [p.process_audio(quiet(seed=i))[1] for i in range(30)]
    assert tail[0] is True and tail[-1] is False                          # does not flicker off instantly


def test_outputs_pcm16_of_same_length_and_survives_bad_input():
    p = AudioProcessor()
    pcm, _ = p.process_audio(loud(1600))
    assert len(pcm) == 1600 * 2
    assert p.process_audio(b'') == (b'', False)
    assert p.process_audio(b'\x00\x01\x02')[0] == b''                      # < 1 sample: ignored, no crash
    nan = np.full(800, np.nan, np.float32).tobytes()
    assert len(p.process_audio(nan)[0]) == 800 * 2
    p.process_audio(loud(1600)); p.process_audio(loud(800))               # chunk size changes mid-stream
    p.process_audio(loud(1600, seed=3), 'float32')
    ints = (np.random.default_rng(0).normal(0, 3000, 1600)).astype(np.int16).tobytes()
    assert len(p.process_audio(ints, 'int16')[0]) == 3200


def test_reset_clears_state():
    p = AudioProcessor()
    for i in range(40):
        p.process_audio(loud(seed=i))
    assert p.is_speaking()
    p.reset()
    assert not p.is_speaking() and p.noise_floor == 0.01
