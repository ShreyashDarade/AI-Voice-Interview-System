"""
Production-ready API views with rate limiting, validation, and error handling.
"""
import logging
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from core.models import Resume, Interview
from proctoring import services as proctor_services
from proctoring.auth import ApiKeyAuthentication, IsIntegrator
from proctoring.models import Tenant
from .serializers import (
    ResumeUploadSerializer, ResumeDetailSerializer, ResumeSerializer,
    InterviewCreateSerializer, InterviewSerializer, HealthSerializer
)
from interview.resume_parser import ResumeParser

logger = logging.getLogger(__name__)

# Error message constants
ERROR_MESSAGES = {
    'interview_not_found': 'Interview not found',
    'resume_not_found': 'Resume not found',
}


class UploadRateThrottle(AnonRateThrottle):
    rate = '10/hour'


class InterviewRateThrottle(AnonRateThrottle):
    rate = '20/hour'


class HealthCheckView(APIView):
    """Liveness/readiness. Public, unauthenticated, reveals no secrets."""
    authentication_classes = []
    permission_classes = []
    throttle_classes = []

    def get(self, request):
        from proctoring import runtime as proctor_runtime
        health = {'status': 'healthy', 'version': settings.APP_VERSION, 'environment': settings.ENVIRONMENT,
                  'timestamp': timezone.now().isoformat(), 'checks': {}}
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            health['checks']['database'] = 'ok'
        except Exception:
            logger.exception("Database health check failed")
            health['checks']['database'] = 'error'
            health['status'] = 'degraded'
        caps = proctor_runtime.capabilities()
        health['checks']['vision'] = 'ok' if caps['video'] else 'unavailable'      # degraded mode: telemetry-only proctoring
        health['checks']['vision_identity'] = 'ok' if caps['identity'] else 'unavailable'
        health['checks']['gemini_key'] = 'configured' if settings.GEMINI_API_KEY else 'missing'
        health['checks']['file_system'] = 'ok' if settings.MEDIA_ROOT.exists() else 'error'
        if health['checks']['file_system'] == 'error':
            health['status'] = 'degraded'
        code = status.HTTP_200_OK if health['status'] == 'healthy' else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(HealthSerializer(health).data | {'checks': health['checks']}, status=code)


class ResumeUploadView(APIView):
    """Upload + parse a resume (offline engine). Unsafe or unreadable files are rejected, never stored."""
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [IsIntegrator]
    throttle_classes = [UploadRateThrottle]

    def post(self, request):
        from resume import ResumeParseError
        from . import resume_service
        serializer = ResumeUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        uploaded = serializer.validated_data['file']
        tenant = request.auth if isinstance(request.auth, Tenant) else None
        resume = Resume.objects.create(file=uploaded, original_filename=uploaded.name[:255],
                                       owner_id=tenant.id if tenant else None)
        try:
            duplicates = resume_service.ingest(resume)
        except ResumeParseError as exc:
            code_status, code = resume_service.error_for(exc)
            logger.warning("Resume rejected (%s): %s", code, type(exc).__name__)
            resume.file.delete(save=False)
            resume.delete()
            return Response({'error': code, 'detail': str(exc)[:300]}, status=code_status)
        except Exception:
            logger.exception("Resume ingestion crashed")
            resume.file.delete(save=False)
            resume.delete()
            return Response({'error': 'ingestion_failed'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        logger.info("Resume %s parsed (confidence %.2f)", resume.id, resume.parse_confidence)
        return Response({**ResumeDetailSerializer(resume).data, 'duplicates': duplicates},
                        status=status.HTTP_201_CREATED)


class ResumeDetailView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [IsIntegrator]

    def get(self, request, resume_id):
        qs = Resume.objects.filter(id=resume_id)
        if isinstance(request.auth, Tenant):
            qs = qs.filter(owner_id=request.auth.id)
        resume = qs.first()
        if resume is None:
            return Response({'error': ERROR_MESSAGES['resume_not_found']}, status=status.HTTP_404_NOT_FOUND)
        return Response(ResumeDetailSerializer(resume).data)


class InterviewStartView(APIView):
    """
    Create an interview *and its proctoring session*.

    The interview stays PENDING until the candidate has given consent and
    started the proctoring session (`/api/proctor/sessions/<id>/start/`); only
    then does it become IN_PROGRESS and the voice socket accepts connections.
    Response carries the short-lived candidate credentials.
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [IsIntegrator]
    throttle_classes = [InterviewRateThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = InterviewCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        resume = Resume.objects.filter(id=serializer.validated_data['resume_id']).first()
        if isinstance(request.auth, Tenant) and resume is not None and resume.owner_id != request.auth.id:
            resume = None                                           # another tenant's resume: indistinguishable from absent
        if resume is None:
            return Response({'error': ERROR_MESSAGES['resume_not_found']}, status=status.HTTP_404_NOT_FOUND)
        if Interview.objects.filter(resume=resume, status__in=[Interview.Status.PENDING,
                                                               Interview.Status.IN_PROGRESS]).exists():
            return Response({'error': 'An interview is already open for this resume'}, status=status.HTTP_409_CONFLICT)

        interview = Interview.objects.create(
            resume=resume, experience_level=serializer.validated_data['experience_level'],
            status=Interview.Status.PENDING)
        tenant = request.auth if isinstance(request.auth, Tenant) else None
        try:
            session = proctor_services.create_session(
                interview=interview, tenant=tenant,
                policy_name=serializer.validated_data.get('policy'),
                overrides=serializer.validated_data.get('policy_overrides'),
                accommodations=serializer.validated_data.get('accommodations', []),
                external_ref=serializer.validated_data.get('external_ref', ''))
        except proctor_services.ProctorError as exc:
            transaction.set_rollback(True)
            return Response({'error': exc.code, 'detail': str(exc)}, status=exc.http_status)
        logger.info('Interview %s created with proctor session %s', interview.id, session.id)
        body = InterviewSerializer(interview).data
        return Response({**body, 'proctor_session_id': str(session.id),
                         **proctor_services.issue_credentials(session),
                         'voice_ws_path': f'/ws/interview/{interview.id}/'}, status=status.HTTP_201_CREATED)


class InterviewStatusView(APIView):
    """Get interview status."""
    
    def get(self, request, interview_id):
        """Retrieve interview status."""
        try:
            interview = Interview.objects.select_related('resume').get(id=interview_id)
            serializer = InterviewSerializer(interview)
            return Response(serializer.data)
        except Interview.DoesNotExist:
            logger.warning(f"Interview not found: {interview_id}")
            return Response(
                {'error': ERROR_MESSAGES['interview_not_found']},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error retrieving interview: {e}", exc_info=True)
            return Response(
                {'error': 'Failed to retrieve interview'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class InterviewEndView(APIView):
    """End an interview."""
    
    @transaction.atomic
    def post(self, request, interview_id):
        """End an interview session."""
        try:
            interview = Interview.objects.select_for_update().get(id=interview_id)
            
            if interview.status != Interview.Status.IN_PROGRESS:
                logger.warning(f"Cannot end interview with status: {interview.status}")
                return Response(
                    {'error': 'Interview is not in progress'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            session = getattr(interview, 'proctor_session', None)
            if session is not None:
                proctor_services.complete_session(session.id)
                interview.refresh_from_db()
            else:
                interview.end()
            logger.info(f"Interview ended: {interview_id}")
            
            serializer = InterviewSerializer(interview)
            return Response(serializer.data)
            
        except Interview.DoesNotExist:
            logger.warning(f"Interview not found: {interview_id}")
            return Response(
                {'error': ERROR_MESSAGES['interview_not_found']},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Failed to end interview: {e}", exc_info=True)
            return Response(
                {'error': 'Failed to end interview', 'detail': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class InterviewEvaluateView(APIView):
    """
    Decision-support summary of the interview transcript (optional LLM, deterministic fallback).
    Never a hire/reject verdict, and integrity data is not part of the input.
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [IsIntegrator]

    def post(self, request, interview_id):
        from llm import evaluation
        interview = Interview.objects.select_related('resume', 'proctor_session').filter(id=interview_id).first()
        session = getattr(interview, 'proctor_session', None) if interview else None
        if interview is None or (isinstance(request.auth, Tenant) and (session is None or session.tenant_id != request.auth.id)):
            return Response({'error': ERROR_MESSAGES['interview_not_found']}, status=status.HTTP_404_NOT_FOUND)
        if interview.status == Interview.Status.PENDING:
            return Response({'error': 'interview_not_started'}, status=status.HTTP_409_CONFLICT)
        topics = [t.get('topic') for t in (interview.resume.probe_plan or {}).get('topics', []) if t.get('topic')] \
            or list(interview.resume.skills or [])[:8]
        result = evaluation.evaluate((interview.session_data or {}).get('transcript', []), topics, interview.experience_level)
        interview.evaluation = result
        interview.save(update_fields=['evaluation', 'updated_at'])
        return Response(result)
