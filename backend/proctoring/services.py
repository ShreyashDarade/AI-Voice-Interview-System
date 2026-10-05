"""
Proctoring service layer -- the only place that mutates sessions.

All state changes run inside ``transaction.atomic`` with the session row locked,
and the risk engine is rebuilt from persisted events every time, so correctness
does not depend on which worker/process handled the request.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_tz
from typing import Iterable, Mapping

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from core.models import Interview

from . import webhooks
from .framework import audit, tokens
from .framework.policy import POLICY_VERSION, Policy, get_policy
from .framework.scoring import CountedEvent, RiskEngine
from .framework.types import Action, Decision, Signal, Verdict
from .models import Evidence, ProctorEvent, ProctorSession, Tenant

logger = logging.getLogger(__name__)

LIMITATIONS = [
    'Signals are evidence for review, not proof of misconduct.',
    'Browser telemetry is reported by the candidate\'s device and can be forged; it is only corroboration.',
    'Gaze and head-pose estimates come from a webcam and have limited accuracy (roughly +/-10 degrees).',
    'Overlay tools that hide from screen capture, and a second device out of camera view, cannot be '
    'detected from video alone.',
]


class ProctorError(Exception):
    """Business-rule violation; ``code`` is machine readable."""

    def __init__(self, code: str, message: str = '', http_status: int = 409):
        super().__init__(message or code)
        self.code, self.http_status = code, http_status


@dataclass
class ApplyResult:
    decision: Decision | None
    persisted: list[ProctorEvent] = field(default_factory=list)
    state: str = ''
    terminated: bool = False


# --------------------------------------------------------------------------- helpers
def policy_for(s: ProctorSession) -> Policy:
    overrides = dict(s.policy_overrides or {})
    if s.accommodations:
        overrides['accommodations'] = list(s.accommodations)
    p = get_policy(s.policy_name, overrides)
    if s.policy_fingerprint and p.fingerprint() != s.policy_fingerprint:
        logger.warning('Policy drift for session %s (stored %s, now %s)', s.id, s.policy_fingerprint, p.fingerprint())
    return p


def engine_for(s: ProctorSession, policy: Policy) -> RiskEngine:
    eng = RiskEngine(policy, started_at=s.started_at.timestamp() if s.started_at else None,
                     floor=Action(s.max_action))
    rows = s.events.filter(counted=True).order_by('seq').values_list(
        'kind', 'category', 'severity', 'points', 'ts', 'strike', 'confidence', 'id')
    from .framework.types import Category, Severity
    eng.replay(CountedEvent(kind=k, category=Category(c), severity=Severity(sev), points=pts, ts=ts,
                            strike=st, confidence=cf, event_id=str(i))
               for k, c, sev, pts, ts, st, cf, i in rows)
    return eng


def _lock(session_id) -> ProctorSession:
    try:
        return ProctorSession.objects.select_for_update().select_related('tenant', 'interview').get(pk=session_id)
    except ProctorSession.DoesNotExist:
        raise ProctorError('session_not_found', 'Session not found', 404)


def append_event(s: ProctorSession, *, kind: str, source: str = 'system', category: str = '', severity: int = 0,
                 confidence: float = 0.0, points: float = 0.0, strike: bool = False, counted: bool = False,
                 action: int | None = None, details: Mapping | None = None, evidence_sha256: str = '',
                 ts: float | None = None) -> ProctorEvent:
    """Caller must hold the session lock and save ``s`` afterwards (we update seq/head in memory)."""
    ev = ProctorEvent(session=s, seq=s.event_seq + 1, ts=ts if ts is not None else time.time(), source=source,
                      kind=kind, category=category, severity=severity, confidence=confidence, points=points,
                      strike=strike, counted=counted, action=s.max_action if action is None else action,
                      details=dict(details or {}), evidence_sha256=evidence_sha256, prev_hash=s.chain_head)
    ev.hash = audit.link(ev.prev_hash, ev.chain_body())
    ev.save()
    s.event_seq, s.chain_head = ev.seq, ev.hash
    return ev


def _wipe_biometrics(s: ProctorSession) -> None:
    s.reference_embedding = None
    ds = dict(s.detector_state or {})
    ds.pop('identity', None)
    s.detector_state = ds


def prepare_evidence_jpeg(jpeg: bytes, max_width: int = 640, quality: int = 70) -> bytes:
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return b''
    if img.shape[1] > max_width:
        img = cv2.resize(img, (max_width, int(img.shape[0] * max_width / img.shape[1])), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b''


# --------------------------------------------------------------------------- creation
@transaction.atomic
def create_session(*, interview: Interview, tenant: Tenant | None = None, policy_name: str | None = None,
                   overrides: Mapping | None = None, accommodations: Iterable[str] = (), external_ref: str = '',
                   reference_embedding=None) -> ProctorSession:
    policy_name = policy_name or (tenant.default_policy if tenant else settings.PROCTOR['DEFAULT_POLICY'])
    overrides = dict(overrides or {})
    accommodations = sorted(set(accommodations))
    try:
        policy = get_policy(policy_name, {**overrides, 'accommodations': accommodations})
    except ValueError as exc:
        raise ProctorError('invalid_policy', str(exc), 400)
    cfg = settings.PROCTOR
    days = (tenant.evidence_retention_days if tenant and tenant.evidence_retention_days else None) or cfg['SESSION_RETENTION_DAYS']
    s = ProctorSession.objects.create(
        tenant=tenant, interview=interview, external_ref=external_ref[:128], policy_name=policy_name,
        policy_overrides=overrides, accommodations=accommodations, policy_fingerprint=policy.fingerprint(),
        policy_version=POLICY_VERSION, reference_embedding=reference_embedding,
        retention_until=timezone.now() + timedelta(days=days))
    append_event(s, kind='session_created', details={'policy': policy_name, 'policy_fingerprint': s.policy_fingerprint,
                                                     'accommodations': accommodations,
                                                     'reference_provided': reference_embedding is not None})
    s.save()
    webhooks.enqueue(s, 'session.created')
    return s


def issue_credentials(s: ProctorSession) -> dict:
    cfg = settings.PROCTOR
    policy = policy_for(s)
    token = tokens.issue(cfg['TOKEN_SECRET'], str(s.id), 'candidate', cfg['TOKEN_TTL_S'])
    return {
        'candidate_token': token,
        'telemetry_key': tokens.derive_session_key(cfg['TOKEN_SECRET'], str(s.id)),
        'expires_in': cfg['TOKEN_TTL_S'],
        'frame_rate_hz': policy.frame_rate_hz,
        'max_frame_bytes': cfg['MAX_FRAME_BYTES'],
        'ws_path': f'/ws/proctor/{s.id}/',
    }


# --------------------------------------------------------------------------- lifecycle
@transaction.atomic
def record_consent(session_id, *, ip: str | None, version: str | None = None) -> ProctorSession:
    s = _lock(session_id)
    if s.is_terminal:
        raise ProctorError('session_ended', 'Session already ended')
    s.consent_at, s.consent_ip = timezone.now(), ip
    s.consent_version = version or settings.PROCTOR['CONSENT_VERSION']
    if s.state == ProctorSession.State.CREATED:
        s.state = ProctorSession.State.PREFLIGHT
    append_event(s, kind='consent_recorded', details={'version': s.consent_version})
    s.save()
    return s


@transaction.atomic
def record_preflight(session_id, result: Mapping) -> ProctorSession:
    s = _lock(session_id)
    append_event(s, kind='preflight', details={'ready': result.get('ready'), 'issues': result.get('issues', [])})
    s.save()
    return s


@transaction.atomic
def start_session(session_id, *, ip: str | None, user_agent: str = '') -> ProctorSession:
    s = _lock(session_id)
    if s.state == ProctorSession.State.ACTIVE:
        return s
    if s.is_terminal:
        raise ProctorError('session_ended', 'Session already ended')
    if not s.consent_at:
        raise ProctorError('consent_required', 'Candidate consent is required before starting', 412)
    policy = policy_for(s)
    if policy.require_identity_reference and s.reference_embedding is None:
        raise ProctorError('reference_required', 'This policy requires a reference photo', 412)
    now = timezone.now()
    s.state, s.started_at = ProctorSession.State.ACTIVE, now
    s.first_ip = s.last_ip = ip
    s.ua_hash = hashlib.sha256(user_agent.encode()).hexdigest()
    s.last_heartbeat = now
    if s.interview.status == Interview.Status.PENDING:
        s.interview.start()
    append_event(s, kind='session_started', details={})
    s.save()
    webhooks.enqueue(s, 'session.started')
    return s


@transaction.atomic
def heartbeat(session_id, *, ip: str | None, user_agent: str = '') -> tuple[ProctorSession, list[dict]]:
    """Returns (session, transport_issues) -- issues are fed to the pipeline as events."""
    s = _lock(session_id)
    issues: list[dict] = []
    if s.is_terminal:
        return s, issues
    ua_hash = hashlib.sha256(user_agent.encode()).hexdigest() if user_agent else s.ua_hash
    if s.ua_hash and ua_hash != s.ua_hash:
        issues.append({'issue': 'ua_changed'})
        s.ua_hash = ua_hash
    if ip and s.last_ip and ip != s.last_ip:
        issues.append({'issue': 'ip_changed', 'from': s.last_ip, 'to': ip})
    if ip:
        s.last_ip = ip
    s.last_heartbeat = timezone.now()
    s.save(update_fields=['last_heartbeat', 'last_ip', 'ua_hash', 'updated_at'])
    return s, issues


@transaction.atomic
def connection_lost(session_id) -> ProctorSession | None:
    s = _lock(session_id)
    if s.state != ProctorSession.State.ACTIVE:
        return s
    s.state, s.disconnected_at = ProctorSession.State.PAUSED, timezone.now()
    append_event(s, kind='connection_lost')
    s.save()
    return s


@transaction.atomic
def connection_restored(session_id, *, ip: str | None = None) -> tuple[ProctorSession, float]:
    s = _lock(session_id)
    if s.is_terminal:
        raise ProctorError('session_ended', 'Session already ended')
    gap = 0.0
    if s.state == ProctorSession.State.PAUSED and s.disconnected_at:
        gap = (timezone.now() - s.disconnected_at).total_seconds()
        s.reconnect_count += 1
        s.state, s.disconnected_at = ProctorSession.State.ACTIVE, None
        append_event(s, kind='connection_restored', details={'gap_s': round(gap, 1), 'reconnects': s.reconnect_count})
    s.last_heartbeat = timezone.now()
    if ip:
        s.last_ip = ip
    s.save()
    return s, gap


def _finish(s: ProctorSession, state: str, verdict: Verdict, reason: str, event_kind: str, webhook: str,
            terminated: bool) -> None:
    now = timezone.now()
    s.state, s.verdict, s.ended_at, s.termination_reason = state, verdict.value, now, reason[:255]
    _wipe_biometrics(s)
    if s.interview.status == Interview.Status.IN_PROGRESS:
        s.interview.strikes = s.strikes
        s.interview.save(update_fields=['strikes', 'updated_at'])
        s.interview.end(terminated=terminated, reason=reason)
    append_event(s, kind=event_kind, details={'reason': reason, 'verdict': verdict.value})
    s.save()
    webhooks.enqueue(s, webhook)


@transaction.atomic
def complete_session(session_id, reason: str = 'candidate_completed') -> ProctorSession:
    s = _lock(session_id)
    if s.is_terminal:
        return s
    eng = engine_for(s, policy_for(s))
    s.cumulative_score = eng.cumulative_score()
    verdict = eng.verdict()
    if verdict == Verdict.FAIL:      # floor already terminate-level
        verdict = Verdict.REVIEW
    _finish(s, ProctorSession.State.COMPLETED, verdict, reason, 'session_completed', 'session.completed', False)
    return s


@transaction.atomic
def terminate_session(session_id, reason: str, *, by: str = 'integrator') -> ProctorSession:
    s = _lock(session_id)
    if s.is_terminal:
        return s
    s.max_action = int(Action.TERMINATE)
    _finish(s, ProctorSession.State.TERMINATED, Verdict.FAIL, f'{by}: {reason}', 'session_terminated',
            'session.terminated', True)
    return s


def expire_stale(now: datetime | None = None) -> dict:
    """Watchdog (``manage.py proctor_watchdog``): pause silent sessions, expire abandoned ones."""
    now = now or timezone.now()
    stats = {'paused': 0, 'expired': 0}
    for s in ProctorSession.objects.filter(state=ProctorSession.State.ACTIVE, last_heartbeat__isnull=False):
        p = policy_for(s)
        if (now - s.last_heartbeat).total_seconds() > p.heartbeat_timeout_s:
            connection_lost(s.id)
            stats['paused'] += 1
    for s in ProctorSession.objects.filter(state=ProctorSession.State.PAUSED, disconnected_at__isnull=False):
        p = policy_for(s)
        if (now - s.disconnected_at).total_seconds() > p.disconnect_grace_s:
            with transaction.atomic():
                s = _lock(s.id)
                if s.state != ProctorSession.State.PAUSED:
                    continue
                # Abandonment is not proof of cheating: always route to a human, never auto-fail.
                _finish(s, ProctorSession.State.EXPIRED, Verdict.REVIEW, 'candidate_disconnected', 'session_expired',
                        'session.expired', False)
            stats['expired'] += 1
    return stats


# --------------------------------------------------------------------------- signals
def _record_evidence(s: ProctorSession, policy: Policy, jpeg: bytes | None, ts: float) -> tuple[bytes, str] | None:
    if not jpeg:
        return None
    qs = s.evidence.all()
    if qs.count() >= policy.max_evidence_per_session:
        return None
    last = qs.order_by('-created_at').first()
    if last and ts - last.created_at.timestamp() < policy.evidence_min_interval_s:
        return None
    data = prepare_evidence_jpeg(jpeg)
    if not data:
        return None
    return data, hashlib.sha256(data).hexdigest()


@transaction.atomic
def apply_signals(session_id, signals: Iterable[Signal], *, jpeg: bytes | None = None,
                  now: float | None = None) -> ApplyResult:
    now = now if now is not None else time.time()
    signals = list(signals)
    s = _lock(session_id)
    # Nothing counts before the candidate has consented and started (pre-flight frames only calibrate).
    if s.is_terminal or s.state in (ProctorSession.State.CREATED, ProctorSession.State.PREFLIGHT):
        return ApplyResult(None, state=s.state, terminated=s.state == ProctorSession.State.TERMINATED)
    policy = policy_for(s)
    eng = engine_for(s, policy)
    persisted: list[ProctorEvent] = []
    decision: Decision | None = None
    prev_floor = eng.floor

    for sig in signals:
        ev = eng.ingest(sig)
        if ev is None:
            continue
        decision = eng.evaluate(sig.ts)
        evidence = _record_evidence(s, policy, jpeg, now) if sig.capture_evidence else None
        row = append_event(s, kind=sig.kind, source=sig.source.value, category=ev.category.value,
                           severity=int(ev.severity), confidence=ev.confidence, points=ev.points,
                           strike=ev.strike, counted=True, action=int(decision.action), details=dict(sig.details),
                           evidence_sha256=evidence[1] if evidence else '', ts=sig.ts)
        if evidence:
            exp = timezone.now() + timedelta(days=settings.PROCTOR['EVIDENCE_RETENTION_DAYS'])
            item = Evidence(session=s, event=row, sha256=evidence[1], size=len(evidence[0]), expires_at=exp)
            item.file.save(f'{row.seq:05d}_{sig.kind}.jpg', ContentFile(evidence[0]), save=False)
            item.save()
        persisted.append(row)
        if decision.action >= Action.TERMINATE:
            break

    if decision is None:
        return ApplyResult(None, state=s.state)

    s.risk_score, s.cumulative_score = decision.score, eng.cumulative_score()
    s.strikes, s.max_action = decision.strikes, int(eng.floor)
    s.verdict = eng.verdict().value
    s.interview.strikes = s.strikes
    s.interview.cheating_events_summary = decision.reasons
    s.interview.save(update_fields=['strikes', 'cheating_events_summary', 'updated_at'])

    terminated = decision.action >= Action.TERMINATE
    if terminated:
        reasons = ', '.join(r['kind'] for r in decision.reasons[:3])
        _finish(s, ProctorSession.State.TERMINATED, Verdict.FAIL, f'policy: {reasons}', 'session_terminated',
                'session.terminated', True)
    else:
        if eng.floor >= Action.FLAG and prev_floor < Action.FLAG:
            webhooks.enqueue(s, 'session.flagged', {'reasons': decision.reasons[:3]})
        s.save()
    return ApplyResult(decision, persisted, s.state, terminated)


@transaction.atomic
def save_runtime_state(session_id, state: Mapping, frames_analyzed: int = 0, frames_dropped: int = 0) -> None:
    s = ProctorSession.objects.select_for_update().filter(pk=session_id).first()
    if not s or s.is_terminal:
        return
    s.baseline = state.get('baseline', {})
    s.detector_state = {**(s.detector_state or {}), **state.get('detectors', {})}
    s.frames_analyzed = max(s.frames_analyzed, frames_analyzed)
    s.frames_dropped = max(s.frames_dropped, frames_dropped)
    s.save(update_fields=['baseline', 'detector_state', 'frames_analyzed', 'frames_dropped', 'updated_at'])


@transaction.atomic
def accept_telemetry_seq(session_id, seq: int) -> tuple[bool, int]:
    """Enforce strictly increasing batch sequence numbers. Returns (accepted, expected_next)."""
    s = _lock(session_id)
    if seq <= s.telemetry_seq:
        return False, s.telemetry_seq + 1
    s.telemetry_seq = seq
    s.save(update_fields=['telemetry_seq'])
    return True, seq + 1


# --------------------------------------------------------------------------- reporting
def candidate_view(s: ProctorSession) -> dict:
    p = policy_for(s)
    return {'session_id': str(s.id), 'state': s.state, 'strikes': s.strikes, 'max_strikes': p.max_strikes,
            'warning_level': int(Action(s.max_action)) if s.max_action < int(Action.TERMINATE) else 4,
            'frame_rate_hz': p.frame_rate_hz}


def verify_chain(s: ProctorSession) -> tuple[bool, int | None]:
    recs = ({'prev_hash': e.prev_hash, 'hash': e.hash, 'body': e.chain_body()}
            for e in s.events.order_by('seq'))
    ok, bad = audit.verify_chain(recs)
    if ok and s.events.exists() and s.events.order_by('-seq').first().hash != s.chain_head:
        return False, s.event_seq        # tail deleted
    return ok, bad


def build_report(s: ProctorSession) -> dict:
    policy = policy_for(s)
    eng = engine_for(s, policy)
    ok, bad = verify_chain(s)
    events = list(s.events.order_by('seq'))
    ev_by_event = {}
    for e in s.evidence.all():
        ev_by_event.setdefault(e.event_id, []).append(e)
    timeline = []
    for e in events:
        timeline.append({
            'seq': e.seq, 'at': datetime.fromtimestamp(e.ts, dt_tz.utc).isoformat(), 'source': e.source,
            'kind': e.kind, 'category': e.category, 'severity': e.severity, 'confidence': round(e.confidence, 3),
            'points': round(e.points, 2), 'counted': e.counted, 'strike': e.strike, 'action_after': e.action,
            'details': e.details, 'evidence_ids': [str(x.id) for x in ev_by_event.get(e.id, [])]})
    kinds: dict[str, int] = {}
    for e in events:
        if e.counted:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
    decision = eng.evaluate(time.time())
    duration = None
    if s.started_at:
        duration = ((s.ended_at or timezone.now()) - s.started_at).total_seconds()
    return {
        'schema': 'proctor-report/1',
        'session': {'id': str(s.id), 'interview_id': str(s.interview_id), 'external_ref': s.external_ref,
                    'state': s.state, 'verdict': s.verdict, 'risk_score': round(s.risk_score, 1),
                    'cumulative_score': round(s.cumulative_score, 1), 'strikes': s.strikes,
                    'max_strikes': policy.max_strikes, 'termination_reason': s.termination_reason,
                    'started_at': s.started_at, 'ended_at': s.ended_at, 'duration_s': duration,
                    'reconnects': s.reconnect_count},
        'policy': {'name': s.policy_name, 'version': s.policy_version, 'fingerprint': s.policy_fingerprint,
                   'drifted': policy.fingerprint() != s.policy_fingerprint, 'accommodations': s.accommodations,
                   'thresholds': {'warn': policy.warn_at, 'flag': policy.flag_at, 'terminate': policy.terminate_at}},
        'consent': {'recorded_at': s.consent_at, 'version': s.consent_version},
        'summary': {'category_scores': decision.category_scores, 'signals_by_kind': kinds,
                    'top_reasons': eng.explain(), 'calibrated': bool((s.baseline or {}).get('ready'))},
        'coverage': {'frames_analyzed': s.frames_analyzed, 'frames_dropped': s.frames_dropped,
                     'duration_s': duration},
        'timeline': timeline,
        'evidence': [{'id': str(x.id), 'event_seq': next((t['seq'] for t in timeline if str(x.id) in t['evidence_ids']), None),
                      'sha256': x.sha256, 'size': x.size, 'created_at': x.created_at, 'expires_at': x.expires_at}
                     for x in s.evidence.all()],
        'integrity': {'chain_ok': ok, 'first_bad_index': bad, 'chain_head': s.chain_head, 'events': s.event_seq},
        'limitations': LIMITATIONS,
    }


# --------------------------------------------------------------------------- retention / erasure
def purge_expired(now: datetime | None = None) -> dict:
    now = now or timezone.now()
    n_ev = n_sess = 0
    for e in Evidence.objects.filter(expires_at__lt=now):
        e.file.delete(save=False)
        e.delete()
        n_ev += 1
    for s in ProctorSession.objects.filter(retention_until__lt=now, erased_at__isnull=True):
        delete_session_data(s)
        n_sess += 1
    return {'evidence_deleted': n_ev, 'sessions_deleted': n_sess}


@transaction.atomic
def delete_session_data(s: ProctorSession) -> None:
    """Hard erase (GDPR Art.17 / BIPA destruction): evidence files, events, biometrics."""
    for e in s.evidence.all():
        e.file.delete(save=False)
    s.delete()
