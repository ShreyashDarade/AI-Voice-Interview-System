"""Authentication for the two kinds of callers.

* Integrators (server-to-server): ``Authorization: Bearer px_<prefix>_<secret>``
  -> ``request.auth`` is the ``Tenant``.
* Candidates (browser): ``Authorization: Bearer v1.<...>`` short-lived session
  token scoped to exactly one proctoring session.
"""
from __future__ import annotations

import hmac

from django.conf import settings
from rest_framework import authentication, exceptions, permissions

from .framework import tokens
from .models import ProctorSession, Tenant


class _Principal:
    """Minimal ``request.user`` for token/API-key callers (DRF throttling keys off ``pk``)."""
    is_authenticated = True
    is_anonymous = False

    def __init__(self, pk):
        self.pk = pk


class ApiKeyAuthentication(authentication.BaseAuthentication):
    keyword = 'Bearer'

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode()
        if not header.startswith('Bearer px_'):
            return None
        key = header[len('Bearer '):].strip()
        parts = key.split('_', 2)
        if len(parts) != 3:
            raise exceptions.AuthenticationFailed('Malformed API key')
        tenant = Tenant.objects.filter(api_key_prefix=parts[1], is_active=True).first()
        if tenant is None or not hmac.compare_digest(tenant.api_key_hash, Tenant.hash_key(key)):
            raise exceptions.AuthenticationFailed('Invalid API key')
        return (_Principal(f'tenant:{tenant.id}'), tenant)

    def authenticate_header(self, request):
        return 'Bearer'


class CandidateTokenAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode()
        if not header.startswith('Bearer v1.'):
            return None
        try:
            claims = tokens.verify(settings.PROCTOR['TOKEN_SECRET'], header[len('Bearer '):].strip(), 'candidate')
        except tokens.TokenError as exc:
            raise exceptions.AuthenticationFailed(str(exc))
        return (_Principal(f"session:{claims.get('sub')}"), claims)

    def authenticate_header(self, request):
        return 'Bearer'


class IsIntegrator(permissions.BasePermission):
    """API key required (unless PROCTOR_REQUIRE_API_KEY is off, i.e. local DEBUG)."""

    def has_permission(self, request, view):
        if isinstance(request.auth, Tenant):
            return True
        return not settings.PROCTOR['REQUIRE_API_KEY']


class IsCandidate(permissions.BasePermission):
    """Candidate token scoped to the session in the URL."""

    def has_permission(self, request, view):
        sid = view.kwargs.get('session_id')
        return isinstance(request.auth, dict) and str(request.auth.get('sub')) == str(sid)


def owned_session(request, session_id) -> ProctorSession:
    """Fetch a session, enforcing tenant isolation for integrators."""
    from django.http import Http404
    s = ProctorSession.objects.select_related('tenant', 'interview').filter(pk=session_id).first()
    if s is None:
        raise Http404
    if isinstance(request.auth, Tenant) and s.tenant_id != request.auth.id:
        raise Http404                      # do not reveal other tenants' sessions
    return s
