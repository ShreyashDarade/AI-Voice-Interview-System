"""
Compact signed credentials for candidate-facing endpoints.

Not JWT on purpose: no algorithm negotiation, no library surface, one purpose.
Format: ``v1.<b64url(json payload)>.<b64url(hmac-sha256)>``

Also provides the per-session *telemetry key* used to sign client event
batches (anti-forgery / anti-replay for third parties; it cannot stop a
candidate who controls their own browser, which is why telemetry is always
treated as untrusted corroboration and never as sole proof).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


class TokenError(Exception):
    pass


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def _sig(secret: bytes, msg: bytes) -> bytes:
    return hmac.new(secret, msg, hashlib.sha256).digest()


def issue(secret: str | bytes, subject: str, scope: str, ttl_s: int, extra: dict | None = None,
          now: float | None = None) -> str:
    secret = secret.encode() if isinstance(secret, str) else secret
    now = now if now is not None else time.time()
    payload = {'sub': subject, 'scp': scope, 'iat': int(now), 'exp': int(now + ttl_s), **(extra or {})}
    body = _b64(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode())
    return f'v1.{body}.{_b64(_sig(secret, f"v1.{body}".encode()))}'


def verify(secret: str | bytes, token: str, scope: str, now: float | None = None) -> dict:
    secret = secret.encode() if isinstance(secret, str) else secret
    try:
        ver, body, sig = token.split('.')
    except (ValueError, AttributeError):
        raise TokenError('malformed token')
    if ver != 'v1':
        raise TokenError('unsupported token version')
    if not hmac.compare_digest(_unb64(sig), _sig(secret, f'{ver}.{body}'.encode())):
        raise TokenError('bad signature')
    try:
        payload = json.loads(_unb64(body))
    except Exception:
        raise TokenError('malformed payload')
    now = now if now is not None else time.time()
    if payload.get('exp', 0) < now:
        raise TokenError('token expired')
    if payload.get('scp') != scope:
        raise TokenError('wrong scope')
    return payload


def derive_session_key(secret: str | bytes, session_id: str) -> str:
    """Per-session HMAC key handed to the client for signing telemetry batches."""
    secret = secret.encode() if isinstance(secret, str) else secret
    return _b64(_sig(secret, f'telemetry-key:{session_id}'.encode()))


def sign_batch(key: str, session_id: str, seq: int, body: bytes | str) -> str:
    body = body.encode() if isinstance(body, str) else body
    msg = f'{session_id}:{seq}:'.encode() + body
    return _b64(hmac.new(key.encode(), msg, hashlib.sha256).digest())


def verify_batch(key: str, session_id: str, seq: int, body: bytes | str, signature: str) -> bool:
    return hmac.compare_digest(sign_batch(key, session_id, seq, body), signature or '')
