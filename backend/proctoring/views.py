"""REST API for proctoring (JSON). Integrator endpoints use API keys, candidate endpoints use session tokens."""
from __future__ import annotations

import logging
import time

from django.conf import settings
from django.http import FileResponse, Http404
from rest_framework import serializers, status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.models import Interview

from . import runtime as rt
from . import services
from .auth import (ApiKeyAuthentication, CandidateTokenAuthentication, IsCandidate, IsIntegrator,
                   owned_session)
from .framework.policy import ACCOMMODATIONS, PRESETS, get_policy
from .models import Evidence, ProctorEvent, ProctorSession, Tenant
from .vision.imaging import BadFrame, decode_jpeg
from .vision.preflight import assess_readiness

logger = logging.getLogger(__name__)


def _ip(request):
    return request.META.get('REMOTE_ADDR')


class IntegratorView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [IsIntegrator]


class CandidateView(APIView):
    authentication_classes = [CandidateTokenAuthentication]
    permission_classes = [IsCandidate]


# --------------------------------------------------------------------------- integrator
class CreateSessionSerializer(serializers.Serializer):
    interview_id = serializers.UUIDField()
    policy = serializers.ChoiceField(choices=sorted(PRESETS), required=False)
    policy_overrides = serializers.DictField(required=False, default=dict)
    accommodations = serializers.ListField(child=serializers.ChoiceField(choices=sorted(ACCOMMODATIONS)),
                                           required=False, default=list)
    external_ref = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    reference_image = serializers.ImageField(required=False)


class SessionCollectionView(IntegratorView):
    parser_classes = [JSONParser, MultiPartParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'proctor_create'

    def post(self, request):
        ser = CreateSessionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        interview = Interview.objects.filter(pk=d['interview_id']).first()
        if interview is None:
            return Response({'error': 'interview_not_found'}, status=404)
        if ProctorSession.objects.filter(interview=interview).exists():
            return Response({'error': 'session_exists'}, status=409)
        embedding = None
        if d.get('reference_image') is not None:
            ex = rt.get_extractor()
            if ex is None or not ex.capabilities()['identity']:
                return Response({'error': 'identity_unavailable',
                                 'detail': 'Identity models are not installed on this server.'}, status=422)
            try:
                embedding = embed_reference(ex, d['reference_image'].read())
            except ValueError as exc:
                return Response({'error': 'reference_image_rejected', 'detail': str(exc)}, status=422)
        tenant = request.auth if isinstance(request.auth, Tenant) else None
        try:
            s = services.create_session(
                interview=interview, tenant=tenant, policy_name=d.get('policy'), overrides=d['policy_overrides'],
                accommodations=d['accommodations'], external_ref=d['external_ref'], reference_embedding=embedding)
        except services.ProctorError as exc:
            return Response({'error': exc.code, 'detail': str(exc)}, status=exc.http_status)
        return Response({'session_id': str(s.id), 'state': s.state, 'policy_fingerprint': s.policy_fingerprint,
                         **services.issue_credentials(s)}, status=status.HTTP_201_CREATED)


def embed_reference(extractor, jpeg: bytes) -> list[float]:
    """Reference photo -> face embedding. The image itself is discarded."""
    import numpy as np
    from .framework.types import Frame
    try:
        img = decode_jpeg(jpeg, target_width=800)
    except BadFrame as exc:
        raise ValueError(str(exc))
    f = extractor.extract(Frame(image=img, ts=time.time(), hints={'want_embedding': True, 'want_objects': False}))
    if f['n_faces'] != 1:
        raise ValueError(f"Reference photo must contain exactly one face (found {f['n_faces']}).")
    if f['embedding'] is None:
        raise ValueError('Could not compute a face embedding from the reference photo.')
    return np.asarray(f['embedding'], dtype=float).round(6).tolist()


class SessionDetailView(IntegratorView):
    def get(self, request, session_id):
        s = owned_session(request, session_id)
        return Response({**services.candidate_view(s), 'verdict': s.verdict, 'risk_score': round(s.risk_score, 1),
                         'cumulative_score': round(s.cumulative_score, 1), 'max_action': s.max_action,
                         'external_ref': s.external_ref, 'created_at': s.created_at, 'started_at': s.started_at,
                         'ended_at': s.ended_at, 'termination_reason': s.termination_reason})

    def delete(self, request, session_id):
        """Erase all data of a session (candidate erasure request / retention)."""
        s = owned_session(request, session_id)
        if not s.is_terminal:
            return Response({'error': 'session_active'}, status=409)
        services.delete_session_data(s)
        return Response(status=204)


class SessionReportView(IntegratorView):
    def get(self, request, session_id):
        return Response(services.build_report(owned_session(request, session_id)))


class SessionEventsView(IntegratorView):
    def get(self, request, session_id):
        s = owned_session(request, session_id)
        after = int(request.query_params.get('after', 0))
        limit = min(int(request.query_params.get('limit', 200)), 1000)
        qs = s.events.filter(seq__gt=after).order_by('seq')[:limit]
        return Response({'events': [{'seq': e.seq, 'ts': e.ts, 'kind': e.kind, 'category': e.category,
                                      'severity': e.severity, 'confidence': e.confidence, 'points': e.points,
                                      'counted': e.counted, 'details': e.details, 'hash': e.hash} for e in qs]})


class SessionTerminateView(IntegratorView):
    def post(self, request, session_id):
        s = owned_session(request, session_id)
        reason = str(request.data.get('reason', 'manual'))[:200]
        s = services.terminate_session(s.id, reason, by='integrator')
        return Response({'state': s.state, 'verdict': s.verdict})


class EvidenceDownloadView(IntegratorView):
    def get(self, request, session_id, evidence_id):
        s = owned_session(request, session_id)
        e = Evidence.objects.filter(pk=evidence_id, session=s).first()
        if e is None:
            raise Http404
        return FileResponse(e.file.open('rb'), content_type='image/jpeg',
                            headers={'Cache-Control': 'private, no-store', 'X-Content-SHA256': e.sha256})


class PolicyCatalogView(IntegratorView):
    def get(self, request):
        return Response({'presets': {n: f().to_dict() for n, f in PRESETS.items()},
                         'accommodations': {k: {'disables_detectors': list(v['detectors']),
                                                'disables_signals': list(v['kinds'])}
                                            for k, v in ACCOMMODATIONS.items()}})


class CapabilitiesView(IntegratorView):
    def get(self, request):
        from .framework.detector import registered
        return Response({**rt.capabilities(), 'detectors': {n: list(c.emits) for n, c in registered().items()}})


# --------------------------------------------------------------------------- candidate
class ConsentView(CandidateView):
    def post(self, request, session_id):
        if request.data.get('accepted') is not True:
            return Response({'error': 'consent_required'}, status=412)
        try:
            s = services.record_consent(session_id, ip=_ip(request), version=request.data.get('version'))
        except services.ProctorError as exc:
            return Response({'error': exc.code}, status=exc.http_status)
        return Response(services.candidate_view(s))


class PreflightView(CandidateView):
    """Upload one JPEG; get back what to fix (lighting, framing, extra people...)."""
    parser_classes = [MultiPartParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'proctor_preflight'

    def post(self, request, session_id):
        f = request.FILES.get('frame')
        if f is None or f.size > settings.PROCTOR['MAX_FRAME_BYTES']:
            return Response({'error': 'frame_required'}, status=400)
        ex = rt.get_extractor()
        if ex is None:
            result = {'ready': True, 'issues': ['camera_unavailable'], 'instructions': [], 'metrics': {}}
        else:
            from .framework.types import Frame
            try:
                img = decode_jpeg(f.read())
            except BadFrame as exc:
                return Response({'error': 'bad_frame', 'detail': str(exc)}, status=400)
            feats = ex.extract(Frame(image=img, ts=time.time(), hints={'want_objects': True}))
            result = assess_readiness(feats)
        services.record_preflight(session_id, result)
        return Response(result)


class StartView(CandidateView):
    def post(self, request, session_id):
        try:
            s = services.start_session(session_id, ip=_ip(request), user_agent=request.META.get('HTTP_USER_AGENT', ''))
        except services.ProctorError as exc:
            return Response({'error': exc.code, 'detail': str(exc)}, status=exc.http_status)
        return Response(services.candidate_view(s))


class CompleteView(CandidateView):
    def post(self, request, session_id):
        s = services.complete_session(session_id)
        return Response({'state': s.state})
