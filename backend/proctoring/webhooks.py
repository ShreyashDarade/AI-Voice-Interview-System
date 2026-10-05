"""Signed webhook delivery from the transactional outbox, with SSRF protection."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
from datetime import timedelta
from urllib.parse import urlparse

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ProctorSession, Tenant, WebhookDelivery

logger = logging.getLogger(__name__)


def is_safe_url(url: str) -> tuple[bool, str]:
    """Reject non-https, credentials in URL and anything resolving to a private/loopback range."""
    p = urlparse(url)
    allow_http = settings.DEBUG
    if p.scheme not in (('https', 'http') if allow_http else ('https',)):
        return False, 'webhook must use https'
    if not p.hostname or p.username or p.password:
        return False, 'invalid webhook host'
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False, 'webhook host does not resolve'
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified) and not allow_http:
            return False, 'webhook host resolves to a non-public address'
    return True, ''


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f'{timestamp}.'.encode() + body, hashlib.sha256).hexdigest()
    return f't={timestamp},v1={mac}'


def enqueue(session: ProctorSession, event_type: str, extra: dict | None = None) -> WebhookDelivery | None:
    t = session.tenant
    if not t or not t.webhook_url or not t.is_active:
        return None
    payload = {
        'type': event_type, 'created': int(time.time()),
        'data': {'session_id': str(session.id), 'interview_id': str(session.interview_id),
                 'external_ref': session.external_ref, 'state': session.state, 'verdict': session.verdict,
                 'risk_score': round(session.risk_score, 1), 'strikes': session.strikes,
                 'audit_chain_head': session.chain_head, **(extra or {})},
    }
    return WebhookDelivery.objects.create(tenant=t, session=session, event_type=event_type, payload=payload)


def deliver_pending(limit: int = 50, now=None) -> dict:
    """Run from a worker/cron (``manage.py proctor_webhooks``). At-least-once delivery."""
    now = now or timezone.now()
    cfg = settings.PROCTOR
    stats = {'sent': 0, 'retry': 0, 'failed': 0}
    with transaction.atomic():
        qs = WebhookDelivery.objects.select_for_update(skip_locked=True).filter(
            status=WebhookDelivery.Status.PENDING, next_attempt_at__lte=now).select_related('tenant')[:limit]
        for d in list(qs):
            ok, err = is_safe_url(d.tenant.webhook_url)
            if ok:
                body = json.dumps(d.payload, separators=(',', ':')).encode()
                ts = int(time.time())
                try:
                    r = httpx.post(d.tenant.webhook_url, content=body, timeout=cfg['WEBHOOK_TIMEOUT_S'],
                                   follow_redirects=False,
                                   headers={'Content-Type': 'application/json', 'X-Proctor-Event': d.event_type,
                                            'X-Proctor-Delivery': str(d.id),
                                            'X-Proctor-Signature': sign(d.tenant.webhook_secret, ts, body)})
                    ok = 200 <= r.status_code < 300
                    err = '' if ok else f'HTTP {r.status_code}'
                except httpx.HTTPError as exc:
                    ok, err = False, type(exc).__name__
            d.attempts += 1
            if ok:
                d.status, d.last_error = WebhookDelivery.Status.SENT, ''
                stats['sent'] += 1
            elif d.attempts >= cfg['WEBHOOK_MAX_ATTEMPTS']:
                d.status, d.last_error = WebhookDelivery.Status.FAILED, err[:255]
                stats['failed'] += 1
            else:
                d.next_attempt_at = now + timedelta(seconds=min(3600, 15 * 2 ** d.attempts))
                d.last_error = err[:255]
                stats['retry'] += 1
            d.save(update_fields=['status', 'attempts', 'next_attempt_at', 'last_error'])
    return stats
