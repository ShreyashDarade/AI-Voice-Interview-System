"""
Proctoring persistence.

Append-only ``ProctorEvent`` rows are hash-chained (see framework/audit.py) and
are the single source of truth: the risk engine's state is always rebuilt from
them, so any worker can serve any request and a restart loses nothing.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid

from django.db import models
from django.utils import timezone

from .framework import audit


class Tenant(models.Model):
    """An integrating customer (an ATS, a hiring platform, ...)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    api_key_prefix = models.CharField(max_length=16, unique=True, editable=False)
    api_key_hash = models.CharField(max_length=64, editable=False)
    is_active = models.BooleanField(default=True)
    default_policy = models.CharField(max_length=20, default='standard')
    webhook_url = models.URLField(blank=True)
    webhook_secret = models.CharField(max_length=64, blank=True, editable=False)
    evidence_retention_days = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    @staticmethod
    def hash_key(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    @classmethod
    def create_with_key(cls, name: str, **kw) -> tuple['Tenant', str]:
        """Returns (tenant, plaintext_key). The plaintext is shown exactly once."""
        prefix = secrets.token_hex(4)
        secret = secrets.token_urlsafe(32)
        key = f'px_{prefix}_{secret}'
        t = cls.objects.create(name=name, api_key_prefix=prefix, api_key_hash=cls.hash_key(key),
                               webhook_secret=secrets.token_hex(24), **kw)
        return t, key


class ProctorSession(models.Model):
    class State(models.TextChoices):
        CREATED = 'created', 'Created'
        PREFLIGHT = 'preflight', 'Pre-flight checks'
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused (disconnected)'
        COMPLETED = 'completed', 'Completed'
        TERMINATED = 'terminated', 'Terminated by policy'
        EXPIRED = 'expired', 'Expired'

    class Verdict(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CLEAR = 'clear', 'Clear'
        REVIEW = 'review', 'Needs human review'
        FAIL = 'fail', 'Failed (terminated)'

    TERMINAL = ('completed', 'terminated', 'expired')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, null=True, blank=True, on_delete=models.SET_NULL, related_name='sessions')
    interview = models.OneToOneField('core.Interview', on_delete=models.CASCADE, related_name='proctor_session')
    external_ref = models.CharField(max_length=128, blank=True, db_index=True)

    # policy in force (kept so a verdict stays reproducible)
    policy_name = models.CharField(max_length=20, default='standard')
    policy_overrides = models.JSONField(default=dict, blank=True)
    policy_fingerprint = models.CharField(max_length=32, blank=True)
    policy_version = models.CharField(max_length=20, blank=True)
    accommodations = models.JSONField(default=list, blank=True)

    state = models.CharField(max_length=12, choices=State.choices, default=State.CREATED, db_index=True)
    verdict = models.CharField(max_length=8, choices=Verdict.choices, default=Verdict.PENDING, db_index=True)
    risk_score = models.FloatField(default=0.0)
    cumulative_score = models.FloatField(default=0.0)
    strikes = models.PositiveSmallIntegerField(default=0)
    max_action = models.PositiveSmallIntegerField(default=0)
    termination_reason = models.CharField(max_length=255, blank=True)

    # consent (GDPR / BIPA / Illinois AIVIA)
    consent_at = models.DateTimeField(null=True, blank=True)
    consent_version = models.CharField(max_length=20, blank=True)
    consent_ip = models.GenericIPAddressField(null=True, blank=True)

    # calibration + per-detector state that must survive reconnects
    baseline = models.JSONField(default=dict, blank=True)
    detector_state = models.JSONField(default=dict, blank=True)
    reference_embedding = models.JSONField(null=True, blank=True)     # biometric template; wiped at end of retention

    # device / network context
    first_ip = models.GenericIPAddressField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    ua_hash = models.CharField(max_length=64, blank=True)
    last_heartbeat = models.DateTimeField(null=True, blank=True, db_index=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    reconnect_count = models.PositiveSmallIntegerField(default=0)
    telemetry_seq = models.BigIntegerField(default=-1)
    frames_analyzed = models.PositiveIntegerField(default=0)
    frames_dropped = models.PositiveIntegerField(default=0)

    # audit chain head
    event_seq = models.PositiveIntegerField(default=0)
    chain_head = models.CharField(max_length=64, default=audit.GENESIS)

    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    retention_until = models.DateTimeField(null=True, blank=True, db_index=True)
    erased_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['state', 'last_heartbeat'], name='proctor_state_hb_idx'),
                   models.Index(fields=['tenant', '-created_at'], name='proctor_tenant_idx')]

    def __str__(self):
        return f'ProctorSession {self.id} [{self.state}/{self.verdict}]'

    @property
    def is_terminal(self) -> bool:
        return self.state in self.TERMINAL


class ProctorEvent(models.Model):
    """Append-only, hash-chained."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ProctorSession, on_delete=models.CASCADE, related_name='events')
    seq = models.PositiveIntegerField()
    ts = models.FloatField()                      # epoch seconds (server clock)
    source = models.CharField(max_length=12)
    kind = models.CharField(max_length=40, db_index=True)
    category = models.CharField(max_length=16, blank=True)
    severity = models.PositiveSmallIntegerField(default=0)
    confidence = models.FloatField(default=0.0)
    points = models.FloatField(default=0.0)       # weight*confidence before decay; 0 for lifecycle events
    strike = models.BooleanField(default=False)
    counted = models.BooleanField(default=False)  # contributes to risk
    action = models.PositiveSmallIntegerField(default=0)   # session action level after this event
    details = models.JSONField(default=dict, blank=True)
    evidence_sha256 = models.CharField(max_length=64, blank=True)
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['session', 'seq']
        constraints = [models.UniqueConstraint(fields=['session', 'seq'], name='proctor_event_seq_uniq')]
        indexes = [models.Index(fields=['session', 'counted', 'ts'], name='proctor_event_counted_idx')]

    def chain_body(self) -> dict:
        return {'seq': self.seq, 'ts': self.ts, 'source': self.source, 'kind': self.kind,
                'category': self.category, 'severity': self.severity, 'confidence': round(self.confidence, 4),
                'points': round(self.points, 4), 'strike': self.strike, 'counted': self.counted,
                'action': self.action, 'details': self.details, 'evidence_sha256': self.evidence_sha256}


def evidence_path(instance: 'Evidence', filename: str) -> str:
    return f'proctor_evidence/{instance.session_id}/{filename}'


class Evidence(models.Model):
    """A single still captured at the moment of a violation. We never keep continuous video."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ProctorSession, on_delete=models.CASCADE, related_name='evidence')
    event = models.ForeignKey(ProctorEvent, null=True, on_delete=models.SET_NULL, related_name='evidence_items')
    file = models.FileField(upload_to=evidence_path)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ['created_at']


class WebhookDelivery(models.Model):
    """Transactional outbox: written in the same transaction as the state change."""
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed (gave up)'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='deliveries')
    session = models.ForeignKey(ProctorSession, on_delete=models.CASCADE, related_name='deliveries')
    event_type = models.CharField(max_length=40)
    payload = models.JSONField()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_error = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
