"""
Integration tests against the real on-device models.

Skipped unless the weights are present (`manage.py fetch_proctor_models`) and a
frontal face photo is supplied via PROCTOR_TEST_PORTRAIT=/path/to/portrait.jpg
(any frontal, well-lit portrait you have the right to use; we do not commit images).
"""
import os
import struct
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from django.conf import settings

from proctoring.framework.pipeline import Pipeline
from proctoring.framework.policy import get_policy
from proctoring.framework.types import Frame
from proctoring.vision import assets
from proctoring.vision.engine import VisionExtractor, euler_from_matrix
from proctoring.vision.imaging import BadFrame, decode_jpeg, jpeg_dimensions
from proctoring.vision.preflight import assess_readiness

PORTRAIT = os.environ.get('PROCTOR_TEST_PORTRAIT', '')
MODEL_DIR = Path(settings.PROCTOR['MODEL_DIR'])
HAVE = PORTRAIT and Path(PORTRAIT).exists() and assets.verify(MODEL_DIR, 'face_landmarker')
pytestmark = pytest.mark.skipif(not HAVE, reason='needs PROCTOR_TEST_PORTRAIT and downloaded models')


@pytest.fixture(scope='module')
def extractor():
    ex = VisionExtractor(MODEL_DIR, pool_size=1)
    yield ex
    ex.close()


@pytest.fixture(scope='module')
def portrait():
    img = cv2.imread(PORTRAIT)
    return cv2.resize(img, (640, int(640 * img.shape[0] / img.shape[1])))


def fr(img, ts=0.0, **hints):
    return Frame(image=img, ts=ts, hints=hints)


def test_euler_conventions_are_the_documented_ones():
    # face turned toward image-right: normal (R[:,2]) gains +x
    a = np.radians(30)
    R = np.eye(4); R[:3, :3] = [[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]]
    assert euler_from_matrix(R)[0] == pytest.approx(30, abs=1e-6)
    b = np.radians(20)                      # tilt up: normal gains +y
    R = np.eye(4); R[:3, :3] = [[1, 0, 0], [0, np.cos(b), np.sin(b)], [0, -np.sin(b), np.cos(b)]]
    assert euler_from_matrix(R)[1] == pytest.approx(20, abs=1e-6)


def test_extracts_single_frontal_face_with_sane_features(extractor, portrait):
    f = extractor.extract(fr(portrait, want_embedding=True))
    p = f['primary']
    assert f['n_faces'] == 1 and p.landmarks.shape == (478, 3)
    assert abs(p.yaw) < 12 and abs(p.pitch) < 15 and abs(p.roll) < 8
    assert abs(p.gaze_h) < 0.5 and 0 <= p.blink <= 1
    assert f['embedding'].shape == (128,) and np.linalg.norm(f['embedding']) == pytest.approx(1, abs=1e-4)
    assert f['meta']['width'] == 640


def test_roll_sign_matches_image_rotation(extractor, portrait):
    H, W = portrait.shape[:2]
    rolls = {}
    for angle in (-20, 20):
        M = cv2.getRotationMatrix2D((W / 2, H / 2), angle, 1.0)           # + = counter-clockwise
        rolls[angle] = extractor.extract(fr(cv2.warpAffine(portrait, M, (W, H), borderMode=cv2.BORDER_REPLICATE)))['primary'].roll
    assert rolls[20] > 10 and rolls[-20] < -10


def test_same_face_embeds_similarly_under_noise_and_brightness(extractor, portrait):
    a = extractor.extract(fr(portrait, want_embedding=True))['embedding']
    rng = np.random.default_rng(0)
    noisy = np.clip(portrait.astype(np.int16) * 0.8 + rng.normal(0, 4, portrait.shape), 0, 255).astype(np.uint8)
    b = extractor.extract(fr(noisy, want_embedding=True))['embedding']
    assert float(a @ b) > 0.8


def test_two_faces_and_readiness_report(extractor, portrait):
    two = np.hstack([portrait, portrait])
    two = cv2.resize(two, (640, int(640 * two.shape[0] / two.shape[1])))
    f = extractor.extract(fr(two))
    assert f['n_faces'] == 2
    r = assess_readiness(f)
    assert 'multiple_faces' in r['issues'] and not r['ready'] and r['instructions']
    ok = assess_readiness(extractor.extract(fr(portrait)))
    assert 'no_face' not in ok['issues'] and 'multiple_faces' not in ok['issues']
    black = assess_readiness(extractor.extract(fr(np.zeros((480, 640, 3), np.uint8))))
    assert 'no_face' in black['issues'] and 'too_dark' in black['issues']


def stream(pipe, imgs, t0=1000.0, dt=0.5, noise=2.0, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for i, img in enumerate(imgs):
        noisy = np.clip(img.astype(np.int16) + rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
        out += pipe.process_frame(Frame(image=noisy, ts=t0 + i * dt, seq=i)).signals
    return out


def kinds(s):
    return {x.kind for x in s}


def test_pipeline_calibrates_and_is_quiet_on_a_normal_candidate(extractor, portrait):
    pipe = Pipeline(get_policy('standard'), extractor)
    sigs = stream(pipe, [portrait] * 60)                                  # 30 s
    assert pipe.baseline.ready and not sigs, kinds(sigs)


def test_pipeline_flags_second_face(extractor, portrait):
    two = cv2.resize(np.hstack([portrait, portrait]), (640, int(640 * portrait.shape[0] / (2 * portrait.shape[1]) * 1)))
    pipe = Pipeline(get_policy('standard'), extractor)
    sigs = stream(pipe, [portrait] * 20 + [two] * 12)
    assert 'multiple_faces' in kinds(sigs)


def test_pipeline_distinguishes_covered_camera_from_absent_person(extractor, portrait):
    gray = np.full((480, 640, 3), 110, np.uint8)
    gray[::16] = 140                                                      # textured, bright, no face
    pipe = Pipeline(get_policy('standard'), extractor)
    absent = kinds(stream(pipe, [portrait] * 20 + [gray] * 20))
    assert 'face_missing' in absent and 'camera_blocked' not in absent
    pipe = Pipeline(get_policy('standard'), extractor)
    covered = kinds(stream(pipe, [portrait] * 20 + [np.zeros((480, 640, 3), np.uint8)] * 20, noise=0))
    assert 'camera_blocked' in covered and 'face_missing' not in covered


def test_pipeline_flags_a_photo_held_to_the_camera(extractor, portrait):
    pipe = Pipeline(get_policy('standard'), extractor)
    sigs = stream(pipe, [portrait] * 110)                                 # 55 s of a perfectly still, never-blinking face
    assert 'static_image_suspected' in kinds(sigs)


def test_pipeline_flags_frozen_frames(extractor, portrait):
    pipe = Pipeline(get_policy('standard'), extractor)
    sigs = stream(pipe, [portrait] * 40, noise=0)                         # bit-identical frames
    assert 'frozen_feed' in kinds(sigs)


def test_per_frame_latency_budget(extractor, portrait):
    f = fr(portrait)
    extractor.extract(f)
    t = time.perf_counter()
    for _ in range(10):
        extractor.extract(fr(portrait, want_embedding=True))
    per = (time.perf_counter() - t) / 10
    assert per < 0.25, f'{per * 1000:.0f} ms/frame on this machine'      # 2 fps budget is 500 ms


def test_decode_guards():
    ok, buf = cv2.imencode('.jpg', np.zeros((120, 160, 3), np.uint8))
    assert jpeg_dimensions(buf.tobytes()) == (160, 120)
    with pytest.raises(BadFrame):
        decode_jpeg(b'GIF89a....')
    with pytest.raises(BadFrame):
        decode_jpeg(b'\xff\xd8\xff' + b'\x00' * 100)
    bomb = bytearray(buf.tobytes())
    i = bomb.index(b'\xff\xc0')
    bomb[i + 5:i + 9] = struct.pack('>HH', 60000, 60000)                  # claims 60000x60000
    with pytest.raises(BadFrame, match='unsupported frame size'):
        decode_jpeg(bytes(bomb))
    with pytest.raises(BadFrame, match='too large'):
        decode_jpeg(b'\xff\xd8\xff' + b'\x00' * 700_000)
