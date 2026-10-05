import time
from datetime import timedelta

import httpx
import pytest
from django.test import override_settings
from django.utils import timezone

from core.models import Interview
from proctoring import services, webhooks
from proctoring.framework.types import Category, Severity
from proctoring.models import Evidence, ProctorEvent, ProctorSession, WebhookDelivery

from proctoring.testing import make_jpeg, make_signal

pytestmark = pytest.mark.django_db


def reload(s):
    return ProctorSession.objects.get(pk=s.id)


# ============================================================================ lifecycle
def test_create_session_validates_policy_and_starts_chain(make_session):
    s = make_session('strict', accommodations=['relaxed_gaze'])
    assert s.state == 'created' and s.event_seq == 1 and s.events.count() == 1
    assert s.events.first().kind == 'session_created' and s.policy_fingerprint
    assert services.verify_chain(s) == (True, None)
    with pytest.raises(services.ProctorError) as e:
        make_session('draconian')
    assert e.value.code == 'invalid_policy'


def test_start_requires_consent_and_moves_interview_to_in_progress(make_session):
    s = make_session()
    with pytest.raises(services.ProctorError) as e:
        services.start_session(s.id, ip='1.1.1.1')
    assert e.value.code == 'consent_required' and e.value.http_status == 412
    services.record_consent(s.id, ip='1.1.1.1')
    s = services.start_session(s.id, ip='1.1.1.1', user_agent='UA')
    assert s.state == 'active' and s.interview.status == Interview.Status.IN_PROGRESS and s.started_at
    assert s.consent_at and s.consent_version
    assert services.start_session(s.id, ip='1.1.1.1').state == 'active'          # idempotent


def test_policy_can_require_reference_photo(make_session):
    s = make_session(overrides={'require_identity_reference': True})
    services.record_consent(s.id, ip=None)
    with pytest.raises(services.ProctorError) as e:
        services.start_session(s.id, ip=None)
    assert e.value.code == 'reference_required'


def test_signals_before_start_are_ignored(make_session):
    s = make_session()
    services.record_consent(s.id, ip=None)                                         # preflight state
    r = services.apply_signals(s.id, [make_signal('phone_detected')])
    assert r.decision is None and not r.persisted and reload(s).strikes == 0


# ============================================================================ signals
def test_apply_signals_persists_counted_events_with_chain_and_updates_session(active_session):
    s = active_session
    now = time.time() + 60                                                          # past the grace period
    r = services.apply_signals(s.id, [make_signal('phone_detected', ts=now, evidence=False)], now=now)
    s = reload(s)
    assert len(r.persisted) == 1 and r.decision.strikes == 1 and s.strikes == 1 and s.risk_score > 0
    assert s.interview.strikes == 1
    ev = s.events.get(kind='phone_detected')
    assert ev.counted and ev.strike and ev.points > 0 and ev.category == 'environment'
    assert services.verify_chain(s) == (True, None)


def test_cooldown_holds_across_calls_because_engine_is_rebuilt_from_db(active_session):
    s, t = active_session, time.time() + 60
    services.apply_signals(s.id, [make_signal('phone_detected', ts=t)])
    r = services.apply_signals(s.id, [make_signal('phone_detected', ts=t + 5)])       # within 30 s cooldown
    assert not r.persisted and reload(s).events.filter(kind='phone_detected').count() == 1
    r = services.apply_signals(s.id, [make_signal('phone_detected', ts=t + 40)])
    assert len(r.persisted) == 1


def test_strike_limit_terminates_session_and_ends_interview(make_session):
    s = make_session('strict')
    services.record_consent(s.id, ip=None); services.start_session(s.id, ip=None)
    t = time.time() + 60
    services.apply_signals(s.id, [make_signal('multiple_faces', ts=t, cat=Category.PRESENCE)])
    r = services.apply_signals(s.id, [make_signal('multiple_faces', ts=t + 30, cat=Category.PRESENCE)])
    s = reload(s)
    assert r.terminated and s.state == 'terminated' and s.verdict == 'fail' and s.ended_at
    assert s.interview.status == Interview.Status.TERMINATED
    assert 'multiple_faces' in s.termination_reason
    assert services.apply_signals(s.id, [make_signal('phone_detected', ts=t + 99)]).decision is None   # ended: no more writes


def test_strict_identity_change_terminates_immediately_and_wipes_biometrics(make_session):
    s = make_session('strict', reference_embedding=[0.1] * 128)
    services.record_consent(s.id, ip=None); services.start_session(s.id, ip=None)
    ServiceS = reload(s); ServiceS.detector_state = {'identity': {'template': [0.2] * 128}}; ServiceS.save()
    r = services.apply_signals(s.id, [make_signal('identity_changed', ts=time.time() + 1, cat=Category.IDENTITY,
                                                  sev=Severity.CRITICAL, conf=0.9)])
    s = reload(s)
    assert r.terminated and s.reference_embedding is None and 'identity' not in s.detector_state


def test_evidence_saved_hashed_bound_into_chain_and_rate_limited(active_session):
    s, t = active_session, time.time() + 60
    jpeg = make_jpeg(800, 600)
    r = services.apply_signals(s.id, [make_signal('phone_detected', ts=t, evidence=True)], jpeg=jpeg)
    ev = r.persisted[0]
    item = Evidence.objects.get(session=s)
    assert item.sha256 == ev.evidence_sha256 and item.size > 0 and item.event_id == ev.id
    assert item.file.read(2) == b'\xff\xd8'
    assert item.expires_at > timezone.now()
    # a second evidence-worthy event inside min interval gets no new image, but is still recorded
    services.apply_signals(s.id, [make_signal('reference_material', ts=t + 1, cat=Category.ENVIRONMENT, evidence=True)], jpeg=jpeg)
    assert Evidence.objects.filter(session=s).count() == 1
    assert services.verify_chain(reload(s)) == (True, None)


def test_flag_webhook_enqueued_once(make_session, tenant):
    tenant.webhook_url = 'https://hooks.example.com/proctor'; tenant.save()
    s = make_session(tenant=tenant)
    services.record_consent(s.id, ip=None); services.start_session(s.id, ip=None)
    t = time.time() + 60
    services.apply_signals(s.id, [make_signal('virtual_camera', ts=t, cat=Category.TAMPERING)])
    services.apply_signals(s.id, [make_signal('devtools_open', ts=t + 100, cat=Category.DEVICE, sev=Severity.MEDIUM)])
    types = list(WebhookDelivery.objects.filter(session=s).values_list('event_type', flat=True))
    assert types.count('session.flagged') == 1 and 'session.started' in types and 'session.created' in types


# ============================================================================ tamper evidence
def test_edit_delete_and_tail_truncation_are_detected(active_session):
    s = active_session
    services.apply_signals(s.id, [make_signal('phone_detected', ts=time.time() + 60)])
    s = reload(s)
    assert services.verify_chain(s) == (True, None)
    e = s.events.get(kind='phone_detected')
    ProctorEvent.objects.filter(pk=e.pk).update(confidence=0.01)                       # edit
    assert services.verify_chain(s)[0] is False
    ProctorEvent.objects.filter(pk=e.pk).update(confidence=e.confidence)
    assert services.verify_chain(s)[0] is True
    last = s.events.order_by('-seq').first()
    last.delete()                                                                      # drop the newest record
    assert services.verify_chain(s)[0] is False


# ============================================================================ connectivity
def test_disconnect_reconnect_and_expiry(active_session):
    s = active_session
    assert services.connection_lost(s.id).state == 'paused'
    s2, gap = services.connection_restored(s.id, ip='10.0.0.1')
    assert s2.state == 'active' and s2.reconnect_count == 1 and gap >= 0
    services.connection_lost(s.id)
    out = services.expire_stale(now=timezone.now() + timedelta(seconds=500))
    s3 = reload(s)
    assert out['expired'] == 1 and s3.state == 'expired' and s3.verdict == 'review'      # abandonment never auto-fails
    assert s3.interview.status in (Interview.Status.COMPLETED, Interview.Status.TERMINATED)


def test_watchdog_pauses_silent_sessions(active_session):
    s = active_session
    out = services.expire_stale(now=timezone.now() + timedelta(seconds=45))
    assert out['paused'] == 1 and reload(s).state == 'paused'


def test_heartbeat_reports_ip_and_ua_changes(active_session):
    _, issues = services.heartbeat(active_session.id, ip='10.0.0.1', user_agent='UA/1')
    assert issues == []
    _, issues = services.heartbeat(active_session.id, ip='203.0.113.9', user_agent='Other/2')
    assert {i['issue'] for i in issues} == {'ip_changed', 'ua_changed'}


def test_telemetry_sequence_must_increase(active_session):
    assert services.accept_telemetry_seq(active_session.id, 0) == (True, 1)
    assert services.accept_telemetry_seq(active_session.id, 1) == (True, 2)
    assert services.accept_telemetry_seq(active_session.id, 1) == (False, 2)             # replay
    assert services.accept_telemetry_seq(active_session.id, 5) == (True, 6)              # gap allowed, reported by caller


# ============================================================================ completion + report
def test_clean_session_completes_clear_and_report_is_complete(active_session):
    s = services.complete_session(active_session.id)
    assert s.state == 'completed' and s.verdict == 'clear' and s.interview.status == Interview.Status.COMPLETED
    rep = services.build_report(reload(s))
    assert rep['schema'] == 'proctor-report/1' and rep['integrity']['chain_ok'] and rep['session']['verdict'] == 'clear'
    assert rep['policy']['fingerprint'] and rep['consent']['recorded_at'] and rep['limitations']
    assert [t['kind'] for t in rep['timeline']][:2] == ['session_created', 'consent_recorded']


def test_session_with_serious_signal_completes_as_review(active_session):
    s = active_session
    services.apply_signals(s.id, [make_signal('phone_detected', ts=time.time() + 60)])
    assert services.complete_session(s.id).verdict == 'review'


def test_integrator_can_terminate(active_session):
    s = services.terminate_session(active_session.id, 'proctor decision')
    assert s.state == 'terminated' and s.verdict == 'fail' and 'integrator' in s.termination_reason


def test_report_marks_policy_drift(active_session, monkeypatch):
    from proctoring.framework import policy as pol
    monkeypatch.setitem(pol.PRESETS, 'standard', lambda: __import__('dataclasses').replace(pol.standard(), warn_at=1.0))
    assert services.build_report(reload(active_session))['policy']['drifted'] is True


# ============================================================================ retention
def test_purge_and_erasure_remove_files(active_session):
    s = active_session
    services.apply_signals(s.id, [make_signal('phone_detected', ts=time.time() + 60, evidence=True)], jpeg=make_jpeg())
    item = Evidence.objects.get(session=s)
    path = item.file.path
    import os
    assert os.path.exists(path)
    Evidence.objects.filter(pk=item.pk).update(expires_at=timezone.now() - timedelta(days=1))
    assert services.purge_expired()['evidence_deleted'] == 1 and not os.path.exists(path)
    ProctorSession.objects.filter(pk=s.pk).update(retention_until=timezone.now() - timedelta(days=1))
    assert services.purge_expired()['sessions_deleted'] == 1
    assert not ProctorSession.objects.filter(pk=s.pk).exists() and not ProctorEvent.objects.filter(session_id=s.id).exists()


# ============================================================================ webhooks
def test_signature_format_and_verification():
    sig = webhooks.sign('sekret', 1700000000, b'{"a":1}')
    import hashlib, hmac
    expect = hmac.new(b'sekret', b'1700000000.{"a":1}', hashlib.sha256).hexdigest()
    assert sig == f't=1700000000,v1={expect}'


@override_settings(DEBUG=False)
@pytest.mark.parametrize('url', ['http://example.com/h', 'https://127.0.0.1/h', 'https://localhost/h',
                                 'https://169.254.169.254/latest/meta-data', 'https://user:pw@example.com/h',
                                 'https://10.1.2.3/h', 'ftp://example.com/x'])
def test_ssrf_unsafe_urls_rejected(url):
    assert webhooks.is_safe_url(url)[0] is False


def test_delivery_success_retry_and_giving_up(active_session, tenant, monkeypatch):
    tenant.webhook_url = 'https://hooks.example.com/p'; tenant.save()
    s = reload(active_session); s.tenant = tenant; s.save()
    WebhookDelivery.objects.all().delete()
    d = webhooks.enqueue(s, 'session.flagged')
    monkeypatch.setattr(webhooks, 'is_safe_url', lambda u: (True, ''))
    calls = []

    def fake_post(url, content, headers, **kw):
        calls.append((url, headers))
        return httpx.Response(200 if len(calls) > 1 else 500, request=httpx.Request('POST', url))
    monkeypatch.setattr(webhooks.httpx, 'post', fake_post)

    assert webhooks.deliver_pending() == {'sent': 0, 'retry': 1, 'failed': 0}
    d.refresh_from_db(); assert d.status == 'pending' and d.attempts == 1 and d.next_attempt_at > timezone.now()
    assert webhooks.deliver_pending()['sent'] == 0                                       # backoff not elapsed
    out = webhooks.deliver_pending(now=timezone.now() + timedelta(hours=1))
    d.refresh_from_db(); assert out['sent'] == 1 and d.status == 'sent'
    h = calls[0][1]
    assert h['X-Proctor-Event'] == 'session.flagged' and h['X-Proctor-Signature'].startswith('t=')
    assert d.payload['data']['session_id'] == str(s.id) and 'audit_chain_head' in d.payload['data']
