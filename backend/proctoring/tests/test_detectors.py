import numpy as np
import pytest

import proctoring.detectors  # noqa: F401  (registers)
from proctoring.detectors.attention import GazeDetector, HeadPoseDetector, PeriodicGlanceDetector, ReadingPatternDetector
from proctoring.detectors.environment import ObjectsDetector
from proctoring.detectors.feed import FeedIntegrityDetector
from proctoring.detectors.identity import IdentityDetector, cosine
from proctoring.detectors.presence import FacePresenceDetector, VideoQualityDetector
from proctoring.vision.baseline import Baseline

from .helpers import ctx, face, obj, ready_baseline, run


def seq(n, dt=0.5, t0=100.0):
    return [t0 + i * dt for i in range(n)]


def kinds(sigs):
    return {s.kind for s in sigs}


# ============================================================================ presence
class TestFacePresence:
    def test_no_face_needs_sustained_absence(self):
        d = FacePresenceDetector({'min_missing_s': 4.0})
        short = run(d, [ctx(t, faces=[]) for t in seq(6)])                    # 2.5 s
        assert not short
        d = FacePresenceDetector({'min_missing_s': 4.0})
        long = run(d, [ctx(t, faces=[]) for t in seq(14)])                    # 6.5 s
        assert kinds(long) == {'face_missing'} and long[-1].capture_evidence

    def test_brief_detector_flicker_does_not_reset_absence(self):
        d = FacePresenceDetector({'min_missing_s': 4.0})
        frames = [ctx(t, faces=[] if i != 4 else [face()]) for i, t in enumerate(seq(14))]
        assert 'face_missing' in kinds(run(d, frames))

    def test_covered_camera_is_not_reported_as_missing_face(self):
        dark = {'width': 640, 'height': 480, 'brightness': 3.0, 'contrast': 1.0, 'sharpness': 0.0}
        d = FacePresenceDetector({'min_missing_s': 2.0})
        assert not run(d, [ctx(t, faces=[], meta=dark) for t in seq(20)])

    def test_second_face_flagged_only_when_large_enough(self):
        d = FacePresenceDetector({'min_multi_s': 2.0})
        big2 = [ctx(t, faces=[face(area=0.12), face(area=0.06, center=(0.8, 0.5))]) for t in seq(10)]
        assert 'multiple_faces' in kinds(run(d, big2))
        d = FacePresenceDetector({'min_multi_s': 2.0})
        tiny2 = [ctx(t, faces=[face(area=0.12), face(area=0.004, center=(0.9, 0.2))]) for t in seq(10)]
        assert not run(d, tiny2)

    def test_static_poster_face_lowers_confidence(self):
        mk = lambda: [ctx(t, faces=[face(area=0.12), face(area=0.05, center=(0.85, 0.3))]) for t in seq(40)]
        live_frames = [ctx(t, faces=[face(area=0.12), face(area=0.05, center=(0.8 + 0.02 * np.sin(t), 0.3 + 0.02 * np.cos(t)))])
                       for t in seq(40)]
        poster = run(FacePresenceDetector(), mk())[-1]
        live = run(FacePresenceDetector(), live_frames)[-1]
        assert poster.confidence < live.confidence * 0.8

    def test_face_cut_off_by_frame_edge(self):
        d = FacePresenceDetector()
        edge = [ctx(t, faces=[face(area=0.12, center=(0.05, 0.5))]) for t in seq(20)]
        assert 'face_out_of_frame' in kinds(run(d, edge))


class TestVideoQuality:
    def test_blocked_camera(self):
        black = {'width': 640, 'height': 480, 'brightness': 2.0, 'contrast': 1.0, 'sharpness': 0.0}
        sigs = run(VideoQualityDetector(), [ctx(t, faces=[], meta=black) for t in seq(10)])
        assert 'camera_blocked' in kinds(sigs)

    def test_dim_room_is_only_a_low_severity_quality_note(self):
        dim = {'width': 640, 'height': 480, 'brightness': 30.0, 'contrast': 20.0, 'sharpness': 80.0}
        sigs = run(VideoQualityDetector(), [ctx(t, meta=dim) for t in seq(50)])
        assert kinds(sigs) == {'poor_video_quality'}


# ============================================================================ attention
class TestGazeAndHead:
    def look(self, yaw=0.0, pitch=0.0, **kw):
        return face(yaw=yaw, pitch=pitch, **kw)

    def test_centered_candidate_never_flagged(self):
        d = GazeDetector()
        assert not run(d, [ctx(t, faces=[self.look()]) for t in seq(100)])

    def test_sustained_look_away_is_flagged_but_glance_is_not(self):
        glance = [ctx(t, faces=[self.look(yaw=40)]) for t in seq(4)] + [ctx(t, faces=[self.look()]) for t in seq(20, t0=102)]
        assert not run(GazeDetector({'min_duration_s': 3.5}), glance)
        away = [ctx(t, faces=[self.look(yaw=40)]) for t in seq(16)]
        assert 'gaze_off_screen' in kinds(run(GazeDetector({'min_duration_s': 3.5}), away))

    def test_downward_gaze_is_classified_as_looking_down(self):
        down = [ctx(t, faces=[self.look(pitch=-30)]) for t in seq(30)]
        k = kinds(run(GazeDetector({'min_duration_s': 3.5}), down))
        assert 'looking_down' in k and 'gaze_off_screen' not in k

    def test_personal_baseline_prevents_false_positive(self):
        """Webcam above the monitor: this candidate's neutral pose is 18 deg off. Relative to baseline they are fine."""
        b = Baseline(); b.values = {'yaw': 0.0, 'pitch': -18.0, 'gaze_h': 0, 'gaze_v': 0, 'area': 0.1}
        b.spread = {k: 0 for k in b.values}; b.ready = True
        frames = [ctx(t, faces=[self.look(pitch=-18)], baseline=b) for t in seq(60)]
        assert not run(GazeDetector({'min_duration_s': 3.5}), frames)
        # same absolute pose with the default baseline would have been flagged
        frames = [ctx(t, faces=[self.look(pitch=-30)]) for t in seq(30)]
        assert run(GazeDetector({'min_duration_s': 3.5}), frames)

    def test_eyes_compensating_head_turn_is_not_gaze_away(self):
        # head turned 20 deg right but eyes rotated back 0.8 * 25 deg -> still looking at screen
        c = [ctx(t, faces=[self.look(yaw=20, gaze_h=-0.8)]) for t in seq(40)]
        assert not run(GazeDetector({'min_duration_s': 3.0}), c)

    def test_head_turned_away(self):
        sigs = run(HeadPoseDetector({'min_duration_s': 3.0}), [ctx(t, faces=[self.look(yaw=45)]) for t in seq(20)])
        assert kinds(sigs) == {'head_turned_away'}


class TestPatterns:
    def test_count_sweeps_finds_sawtooth(self):
        t = np.arange(0, 30, 0.5)
        x = (t % 5) / 5 * 0.5                              # slow rise 0->0.5 over 5 s then snap back
        assert len(ReadingPatternDetector.count_sweeps(t, x, 0.18)) >= 4
        rng = np.random.default_rng(1)
        assert len(ReadingPatternDetector.count_sweeps(t, rng.normal(0, 0.05, t.size), 0.18)) < 2
        assert len(ReadingPatternDetector.count_sweeps(t, np.full(t.size, 0.2), 0.18)) == 0

    def test_reading_detector_end_to_end(self):
        ts = seq(80, t0=1000)
        reading = [ctx(t, faces=[face(gaze_h=((t - 1000) % 5) / 5 * 0.5)]) for t in ts]
        sigs = run(ReadingPatternDetector(), reading)
        assert 'reading_pattern' in kinds(sigs) and sigs[-1].confidence >= 0.55
        rng = np.random.default_rng(3)
        natural = [ctx(t, faces=[face(gaze_h=float(rng.normal(0, 0.06)))]) for t in ts]
        assert not run(ReadingPatternDetector(), natural)

    def test_reading_requires_still_head(self):
        ts = seq(80, t0=1000)
        moving = [ctx(t, faces=[face(yaw=15 * np.sin(t), gaze_h=((t - 1000) % 5) / 5 * 0.5)]) for t in ts]
        assert not run(ReadingPatternDetector(), moving)

    def test_periodic_glances_every_20_seconds(self):
        frames = []
        for t in seq(400, t0=0):                           # 200 s
            away = (t % 20) < 2.0
            frames.append(ctx(t, faces=[face(yaw=45 if away else 0)]))
        sigs = run(PeriodicGlanceDetector(), frames)
        assert 'periodic_glance' in kinds(sigs)

    def test_irregular_glances_are_not_periodic(self):
        rng = np.random.default_rng(5)
        starts = np.cumsum(rng.uniform(8, 45, 12))
        frames = []
        for t in seq(400, t0=0):
            away = any(s <= t < s + 2 for s in starts)
            frames.append(ctx(t, faces=[face(yaw=45 if away else 0)]))
        assert not run(PeriodicGlanceDetector(), frames)


# ============================================================================ environment
class TestObjects:
    def test_phone_needs_repeated_sightings(self):
        d = ObjectsDetector({'window': 5, 'min_hits': 3})
        one = [ctx(100, objects=[obj('cell phone', 0.9)], objects_ran=True)] + \
              [ctx(100 + i, objects=[], objects_ran=True) for i in range(1, 8)]
        assert not run(d, one)
        d = ObjectsDetector({'window': 5, 'min_hits': 3})
        many = [ctx(100 + i, objects=[obj('cell phone', 0.8)], objects_ran=True) for i in range(5)]
        sigs = run(d, many)
        assert kinds(sigs) == {'phone_detected'} and sigs[-1].capture_evidence

    def test_low_score_or_tiny_phone_ignored(self):
        d = ObjectsDetector({'window': 3, 'min_hits': 2})
        assert not run(d, [ctx(100 + i, objects=[obj('cell phone', 0.3)], objects_ran=True) for i in range(6)])
        assert not run(d, [ctx(100 + i, objects=[obj('cell phone', 0.9, area=0.0003)], objects_ran=True) for i in range(6)])

    def test_frames_without_object_inference_do_not_count(self):
        d = ObjectsDetector({'window': 3, 'min_hits': 2})
        assert not run(d, [ctx(100 + i, objects=[], objects_ran=False) for i in range(10)])

    def test_extra_body_without_face(self):
        d = ObjectsDetector({'window': 4, 'min_hits': 2})
        two = [obj('person', 0.9, area=0.2), obj('person', 0.8, area=0.15)]
        sigs = run(d, [ctx(100 + i, faces=[face()], objects=two, objects_ran=True) for i in range(8)])
        assert 'additional_person' in kinds(sigs)

    def test_book_is_reference_material(self):
        d = ObjectsDetector({'window': 3, 'min_hits': 2})
        sigs = run(d, [ctx(100 + i, objects=[obj('book', 0.8)], objects_ran=True) for i in range(5)])
        assert 'reference_material' in kinds(sigs)


# ============================================================================ identity
def unit(rng, dim=128):
    v = rng.normal(size=dim)
    return (v / np.linalg.norm(v)).astype(np.float32)


def near(v, rng, noise=0.03):
    w = v + rng.normal(0, noise, v.shape)
    return (w / np.linalg.norm(w)).astype(np.float32)


class TestIdentity:
    def feed(self, det, embs, t0=100.0, dt=4.0, **faceargs):
        out = []
        for i, e in enumerate(embs):
            out.extend(det.process(ctx(t0 + i * dt, faces=[face(**faceargs)], embedding=e)))
        return out

    def test_same_person_never_flagged(self):
        rng = np.random.default_rng(0)
        me = unit(rng)
        det = IdentityDetector()
        assert not self.feed(det, [near(me, rng) for _ in range(40)])
        assert det.template is not None and det.last_similarity > 0.8

    def test_person_swap_is_detected_after_confirmation(self):
        rng = np.random.default_rng(1)
        me, other = unit(rng), unit(rng)
        det = IdentityDetector()
        self.feed(det, [near(me, rng) for _ in range(8)])
        sigs = self.feed(det, [near(other, rng) for _ in range(6)], t0=200)
        assert kinds(sigs) == {'identity_changed'} and sigs[0].capture_evidence
        assert sigs[0].confidence >= 0.7

    def test_single_bad_embedding_is_tolerated(self):
        rng = np.random.default_rng(2)
        me, other = unit(rng), unit(rng)
        det = IdentityDetector()
        embs = [near(me, rng) for _ in range(8)] + [other] + [near(me, rng) for _ in range(6)]
        assert not self.feed(det, embs)

    def test_reference_photo_mismatch(self):
        rng = np.random.default_rng(3)
        me, ref = unit(rng), unit(rng)
        det = IdentityDetector(); det.set_reference(ref)
        sigs = self.feed(det, [near(me, rng) for _ in range(6)])
        assert 'identity_mismatch' in kinds(sigs)
        det2 = IdentityDetector(); det2.set_reference(near(me, rng, 0.06))
        assert not self.feed(det2, [near(me, rng) for _ in range(6)])

    def test_enrolment_with_two_people_is_rejected_not_trusted(self):
        rng = np.random.default_rng(4)
        a, b = unit(rng), unit(rng)
        det = IdentityDetector()
        self.feed(det, [near(a, rng), near(b, rng), near(a, rng), near(b, rng), near(a, rng)])
        assert det.template is None                          # inconsistent -> keep collecting

    def test_turned_or_blinking_faces_are_not_used(self):
        rng = np.random.default_rng(5)
        det = IdentityDetector()
        self.feed(det, [unit(rng) for _ in range(10)], yaw=40)
        self.feed(det, [unit(rng) for _ in range(10)], blink=0.9)
        assert det.template is None and not det._enroll

    def test_state_survives_reconnect_and_requests_embeddings_sparsely(self):
        rng = np.random.default_rng(6)
        me = unit(rng)
        det = IdentityDetector()
        self.feed(det, [near(me, rng) for _ in range(6)])
        st = det.export_state()
        det2 = IdentityDetector(); det2.import_state(st)
        assert cosine(det2.template, det.template) == pytest.approx(1.0, abs=1e-5)
        det2._last_embed_ts = 1000.0
        assert det2.hints(1001.0) == {'want_embedding': False} and det2.hints(1005.0) == {'want_embedding': True}


# ============================================================================ feed integrity
class TestFeedIntegrity:
    def noise_thumb(self, rng, base=100.0):
        return (base + rng.normal(0, 1.0, (24, 24))).astype(np.float32)

    def test_live_noisy_feed_is_clean(self):
        rng = np.random.default_rng(0)
        d = FeedIntegrityDetector()
        frames = []
        for i, t in enumerate(seq(120)):          # a living subject: small motion, periodic blinks
            frames.append(ctx(t, faces=[face(yaw=1.5 * rng.normal(), pitch=1.0 * rng.normal(), gaze_h=0.07 * rng.normal(),
                                             blink=0.8 if i % 8 == 0 else 0.1, mouth=0.04 * abs(rng.normal()))],
                              thumb=self.noise_thumb(rng)))
        assert not run(d, frames)

    def test_frozen_feed(self):
        rng = np.random.default_rng(0)
        frozen = self.noise_thumb(rng)
        sigs = run(FeedIntegrityDetector({'frozen_s': 6}), [ctx(t, thumb=frozen.copy()) for t in seq(40)])
        assert 'frozen_feed' in kinds(sigs)

    def test_static_photo_with_sensor_noise_is_flagged(self):
        rng = np.random.default_rng(1)
        d = FeedIntegrityDetector()
        frames = [ctx(t, faces=[face(yaw=0.05 * rng.normal(), pitch=0.1 * rng.normal(), gaze_h=0.01 * rng.normal(),
                                       gaze_v=0.01 * rng.normal(), blink=0.2, mouth=0.02 + 0.003 * rng.normal())],
                      thumb=self.noise_thumb(rng)) for t in seq(130)]
        assert 'static_image_suspected' in kinds(run(d, frames))

    def test_live_person_who_blinks_and_moves_is_not_static(self):
        rng = np.random.default_rng(2)
        d = FeedIntegrityDetector()
        frames = []
        for i, t in enumerate(seq(130)):
            blink = 0.8 if i % 9 == 0 else 0.1
            frames.append(ctx(t, faces=[face(yaw=2 * rng.normal(), pitch=1.5 * rng.normal(), gaze_h=0.08 * rng.normal(),
                                             blink=blink, mouth=0.05 * abs(rng.normal()))],
                              thumb=self.noise_thumb(rng)))
        assert 'static_image_suspected' not in kinds(run(d, frames))

    def test_recorded_loop_is_detected_but_static_scene_is_not(self):
        rng = np.random.default_rng(3)
        loop_len = 40                                         # 20 s clip
        clip = [(100 + 40 * np.sin(np.linspace(0, 6, 24 * 24)).reshape(24, 24) * rng.uniform(0.5, 1.5) * k % 7
                 + rng.normal(0, 3, (24, 24))).astype(np.float32) for k in range(loop_len)]
        clip = [c + 15 * np.roll(np.eye(24, dtype=np.float32), k, axis=1) for k, c in enumerate(clip)]   # motion
        frames = [ctx(100 + i * 0.5, thumb=clip[i % loop_len].copy()) for i in range(200)]
        assert 'feed_loop_suspected' in kinds(run(FeedIntegrityDetector(), frames))
        still = [ctx(100 + i * 0.5, thumb=self.noise_thumb(rng)) for i in range(200)]
        assert 'feed_loop_suspected' not in kinds(run(FeedIntegrityDetector(), still))

    def test_non_monotonic_client_clock(self):
        d = FeedIntegrityDetector()
        sigs = run(d, [ctx(100, client_ts=10.0), ctx(100.5, client_ts=10.5), ctx(101, client_ts=9.0)])
        assert [s.kind for s in sigs] == ['frame_timing_anomaly']


# ============================================================================ baseline
class TestBaseline:
    def feats(self, **kw):
        f = face(**kw)
        return {'n_faces': 1, 'primary': f}

    def test_calibrates_after_clean_seconds_using_median(self):
        b = Baseline(duration_s=4.0, min_samples=4)
        for i, t in enumerate(seq(10)):
            b.update(t, self.feats(yaw=10 + (30 if i == 3 else 0), pitch=-5))
        assert b.ready and b.get('yaw') == pytest.approx(10, abs=0.1) and b.get('pitch') == -5

    def test_never_calibrates_with_two_faces_or_no_face(self):
        b = Baseline(duration_s=2.0, min_samples=2)
        for t in seq(20):
            b.update(t, {'n_faces': 2, 'primary': face()})
            b.update(t, {'n_faces': 0, 'primary': None})
        assert not b.ready

    def test_persistence(self):
        b = Baseline(duration_s=1.0, min_samples=2)
        for t in seq(8):
            b.update(t, self.feats(yaw=7))
        b2 = Baseline.from_dict(b.to_dict())
        assert b2.ready and b2.get('yaw') == pytest.approx(7)
