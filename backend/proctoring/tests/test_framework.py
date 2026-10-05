import dataclasses
import pytest

from proctoring.framework import audit, tokens
from proctoring.framework.policy import ACCOMMODATIONS, BASE_RULES, get_policy
from proctoring.framework.scoring import RiskEngine
from proctoring.framework.types import Action, Category, Severity, Signal, Source, Verdict
from proctoring.framework.util import SlidingHits, Sustained


def sig(kind, ts, conf=0.9, cat=Category.PRESENCE, sev=Severity.MEDIUM, **kw):
    return Signal(kind=kind, category=cat, source=Source.VISION, confidence=conf, severity=sev, ts=ts, **kw)


# ----------------------------------------------------------------------------- policy
def test_presets_are_ordered_by_strictness():
    l, s, st = get_policy('lenient'), get_policy('standard'), get_policy('strict')
    assert l.terminate_at > s.terminate_at > st.terminate_at
    assert l.rules['multiple_faces'].weight < s.rules['multiple_faces'].weight < st.rules['multiple_faces'].weight
    assert st.max_strikes < s.max_strikes < l.max_strikes
    assert not l.strikes_terminate and s.strikes_terminate


def test_every_emitted_kind_has_a_rule():
    import proctoring.detectors  # noqa: F401
    from proctoring.framework.detector import registered
    missing = {k for cls in registered().values() for k in cls.emits} - set(BASE_RULES)
    assert not missing, f'detector kinds without a policy rule: {missing}'


def test_unknown_preset_rejected():
    with pytest.raises(ValueError):
        get_policy('draconian')


def test_overrides_and_fingerprint_stability():
    a = get_policy('standard')
    b = get_policy('standard', {'warn_at': 5, 'rule_weights': {'phone_detected': 50}})
    assert a.fingerprint() == get_policy('standard').fingerprint()
    assert a.fingerprint() != b.fingerprint()
    assert b.warn_at == 5 and b.rules['phone_detected'].weight == 50
    assert get_policy('standard', {'not_a_field': 1}).fingerprint() == a.fingerprint()    # unknown keys ignored


def test_accommodations_disable_detectors_and_rules():
    p = get_policy('strict', {'accommodations': ['relaxed_gaze']})
    assert 'gaze' not in p.detectors and 'gaze_off_screen' not in p.rules
    assert 'multiple_faces' in p.rules and 'face_presence' in p.detectors      # integrity-critical stays on
    with pytest.raises(ValueError):
        get_policy('standard', {'accommodations': ['nope']})
    assert set(ACCOMMODATIONS) >= {'relaxed_gaze', 'assistive_technology'}


# ----------------------------------------------------------------------------- scoring
def engine(preset='standard', t0=1000.0, **ov):
    e = RiskEngine(get_policy(preset, ov or None))
    e.start(t0)
    return e


def test_low_confidence_and_unknown_kinds_ignored():
    e = engine()
    assert e.ingest(sig('multiple_faces', 1100, conf=0.2)) is None
    assert e.ingest(sig('made_up_kind', 1100)) is None
    assert e.evaluate(1100).action == Action.NONE


def test_cooldown_counts_a_persistent_state_once_per_window():
    e = engine()
    counted = [e.ingest(sig('multiple_faces', 1100 + i)) for i in range(0, 25)]      # 25 s of the same state
    assert sum(c is not None for c in counted) == 2                                  # cooldown 20 s
    assert e.strikes == 2


def test_grace_period_mutes_soft_signals_but_not_tampering():
    e = engine()                                                                      # grace 20 s
    assert e.ingest(sig('gaze_off_screen', 1005, cat=Category.ATTENTION)) is None
    assert e.ingest(sig('frozen_feed', 1005, cat=Category.TAMPERING, sev=Severity.HIGH)) is not None


def test_category_cap_stops_one_sensor_from_dominating():
    e = engine('lenient')
    for i in range(200):
        e.ingest(sig('gaze_off_screen', 1100 + i * 30, conf=1.0, cat=Category.ATTENTION))
    d = e.evaluate(1100 + 200 * 30)
    assert d.category_scores['attention'] <= get_policy('lenient').category_caps['attention']
    assert d.action < Action.TERMINATE


def test_decay_lowers_live_score_but_not_cumulative():
    e = engine()
    e.ingest(sig('phone_detected', 1100, cat=Category.ENVIRONMENT, sev=Severity.HIGH))
    live_now, live_later = e.live_score(1100), e.live_score(1100 + 3000)
    assert live_later < live_now * 0.01
    assert e.cumulative_score() == pytest.approx(live_now, rel=1e-6)


def test_strikes_terminate_under_strict_but_not_lenient():
    strict, lenient = engine('strict'), engine('lenient')
    for e in (strict, lenient):
        for i in range(3):
            e.ingest(sig('multiple_faces', 1100 + i * 60))
    assert strict.evaluate(1300).action == Action.TERMINATE
    assert lenient.evaluate(1300).action < Action.TERMINATE


def test_hard_action_signal_flags_immediately_and_is_sticky():
    e = engine()
    e.ingest(sig('virtual_camera', 1100, cat=Category.TAMPERING, sev=Severity.HIGH))
    d = e.evaluate(1100)
    assert d.action >= Action.FLAG and d.new_action
    d2 = e.evaluate(1100 + 100000)                      # long after the score decayed
    assert d2.action >= Action.FLAG and not d2.new_action
    assert d2.verdict == Verdict.REVIEW


def test_strict_hard_terminate_on_identity_change():
    e = engine('strict')
    e.ingest(sig('identity_changed', 1100, cat=Category.IDENTITY, sev=Severity.CRITICAL))
    d = e.evaluate(1100)
    assert d.action == Action.TERMINATE and d.verdict == Verdict.FAIL
    assert 'could not be completed' in d.message
    assert 'identity' not in d.message.lower()          # never reveals detector internals


def test_candidate_messages_are_generic_and_actionable():
    e = engine()
    e.ingest(sig('phone_detected', 1100, cat=Category.ENVIRONMENT, sev=Severity.HIGH))
    msg = e.evaluate(1100).message
    assert 'phone' not in msg.lower() and 'threshold' not in msg.lower()
    assert 'remove' in msg.lower() or 'notice' in msg.lower()


def test_replay_rebuilds_identical_state():
    e = engine()
    for i, k in enumerate(['multiple_faces', 'phone_detected', 'tab_hidden']):
        e.ingest(sig(k, 1100 + i * 60, cat=BASE_RULES[k].category))
    rebuilt = RiskEngine(e.policy, started_at=1000.0, floor=e.floor)
    rebuilt.replay(e.events)
    assert rebuilt.evaluate(1500).score == e.evaluate(1500).score
    assert rebuilt.strikes == e.strikes


def test_clean_session_is_clear_and_explainable():
    e = engine()
    assert e.evaluate(2000).verdict == Verdict.CLEAR
    e.ingest(sig('gaze_off_screen', 1100, cat=Category.ATTENTION, sev=Severity.LOW))
    assert e.verdict() == Verdict.CLEAR
    top = e.explain()[0]
    assert top['kind'] == 'gaze_off_screen' and top['count'] == 1 and top['points'] > 0


# ----------------------------------------------------------------------------- util
def test_sustained_resets_on_gap_and_tolerates_flicker():
    s = Sustained(max_gap_s=3.0, off_grace_s=1.0)
    assert s.update(True, 0) == 0
    assert s.update(True, 2) == 2
    assert s.update(False, 2.5) == pytest.approx(2.5)      # within grace
    assert s.update(False, 4.0) == 0                       # grace exceeded
    s.update(True, 5)
    assert s.update(True, 20) == 0                         # gap > max_gap -> restart


def test_sliding_hits():
    h = SlidingHits(window=5, min_hits=3)
    assert [h.push(x) for x in (1, 0, 1, 0, 1)] == [False, False, False, False, True]


# ----------------------------------------------------------------------------- tokens + audit
def test_token_roundtrip_and_failures():
    t = tokens.issue('k', 'sess-1', 'candidate', 60, now=1000)
    assert tokens.verify('k', t, 'candidate', now=1010)['sub'] == 'sess-1'
    with pytest.raises(tokens.TokenError, match='expired'):
        tokens.verify('k', t, 'candidate', now=2000)
    with pytest.raises(tokens.TokenError, match='scope'):
        tokens.verify('k', t, 'admin', now=1010)
    with pytest.raises(tokens.TokenError, match='signature'):
        tokens.verify('other', t, 'candidate', now=1010)
    v, body, sig_ = t.split('.')
    forged = f'{v}.{body[:-2]}xx.{sig_}'
    with pytest.raises(tokens.TokenError):
        tokens.verify('k', forged, 'candidate', now=1010)
    with pytest.raises(tokens.TokenError):
        tokens.verify('k', 'garbage', 'candidate')


def test_batch_signature_binds_session_seq_and_body():
    key = tokens.derive_session_key('k', 's1')
    sig_ = tokens.sign_batch(key, 's1', 7, '[]')
    assert tokens.verify_batch(key, 's1', 7, '[]', sig_)
    assert not tokens.verify_batch(key, 's1', 8, '[]', sig_)           # replay with new seq
    assert not tokens.verify_batch(key, 's2', 7, '[]', sig_)           # other session
    assert not tokens.verify_batch(key, 's1', 7, '[{"x":1}]', sig_)    # altered body
    assert tokens.derive_session_key('k', 's1') != tokens.derive_session_key('k', 's2')


def test_audit_chain_detects_edit_delete_and_reorder():
    recs, prev = [], audit.GENESIS
    for i in range(5):
        body = {'seq': i + 1, 'kind': 'x', 'v': i}
        h = audit.link(prev, body)
        recs.append({'prev_hash': prev, 'hash': h, 'body': body})
        prev = h
    assert audit.verify_chain(recs) == (True, None)
    edited = [dict(r) for r in recs]
    edited[2] = {**edited[2], 'body': {**edited[2]['body'], 'v': 99}}
    assert audit.verify_chain(edited) == (False, 2)
    assert audit.verify_chain(recs[:2] + recs[3:])[0] is False
    assert audit.verify_chain([recs[1], recs[0]] + recs[2:])[0] is False
