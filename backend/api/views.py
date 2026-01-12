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

from core.models import Resume, Interview, CheatingEvent
from .serializers import (
    ResumeUploadSerializer, ResumeDetailSerializer, ResumeSerializer,
    InterviewCreateSerializer, InterviewSerializer,
    CheatingReportSerializer, HealthSerializer
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


class CheatingRateThrottle(AnonRateThrottle):
    rate = '50/hour'


class HealthCheckView(APIView):
    """Health check endpoint with dependency validation."""
    
    def get(self, request):
        """Check system health."""
        health_data = {
            'status': 'healthy',
            'version': settings.APP_VERSION,
            'environment': settings.ENVIRONMENT,
            'timestamp': timezone.now().isoformat(),
            'checks': {}
        }
        
        # Database check
        if settings.HEALTH_CHECK_DATABASE:
            try:
                from django.db import connection
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                health_data['checks']['database'] = 'ok'
            except Exception as e:
                logger.error(f"Database health check failed: {e}")
                health_data['checks']['database'] = 'error'
                health_data['status'] = 'degraded'
        
        # Gemini API check (optional)
        if settings.HEALTH_CHECK_GEMINI_API and settings.GEMINI_API_KEY:
            health_data['checks']['gemini_api'] = 'configured'
        else:
            health_data['checks']['gemini_api'] = 'not_configured'
        
        # File system check
        try:
            settings.MEDIA_ROOT.exists()
            health_data['checks']['file_system'] = 'ok'
        except Exception as e:
            logger.error(f"File system check failed: {e}")
            health_data['checks']['file_system'] = 'error'
            health_data['status'] = 'degraded'
        
        serializer = HealthSerializer(health_data)
        status_code = status.HTTP_200_OK if health_data['status'] == 'healthy' else status.HTTP_503_SERVICE_UNAVAILABLE
        
        return Response(serializer.data, status=status_code)


class ResumeUploadView(APIView):
    """Upload and parse resume with validation."""
    throttle_classes = [UploadRateThrottle]
    
    def post(self, request):
        """Upload a resume file."""
        serializer = ResumeUploadSerializer(data=request.data)
        
        if not serializer.is_valid():
            logger.warning(f"Resume upload validation failed: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        uploaded_file = serializer.validated_data['file']
        
        try:
            # Create resume object
            resume = Resume.objects.create(
                file=uploaded_file,
                original_filename=uploaded_file.name
            )
            
            # Parse resume asynchronously in production, sync for now
            parser = ResumeParser()
            try:
                parsed_data = parser.parse(resume.file.path)
                
                # Update resume with parsed data
                resume.raw_text = parsed_data.get('raw_text', '')
                resume.parsed_data = parsed_data
                resume.candidate_name = parsed_data.get('name', '')[:255]
                resume.email = parsed_data.get('email', '')[:254]
                resume.phone = parsed_data.get('phone', '')[:50]
                resume.experience_years = min(parsed_data.get('experience_years', 0), 50)
                resume.skills = parsed_data.get('skills', [])
                resume.education = parsed_data.get('education', [])
                resume.work_history = parsed_data.get('work_history', [])
                resume.save()
                
                logger.info(f"Resume uploaded and parsed successfully: {resume.id}")
                
            except Exception as e:
                logger.error(f"Resume parsing failed: {e}", exc_info=True)
                # Don't fail upload, just log parsing error
                resume.parsed_data = {'parsing_error': str(e)}
                resume.save()
            
            serializer = ResumeDetailSerializer(resume)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"Resume upload failed: {e}", exc_info=True)
            return Response(
                {'error': 'Failed to upload resume', 'detail': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ResumeDetailView(APIView):
    """Get resume details."""
    
    def get(self, request, resume_id):
        """Retrieve resume details."""
        try:
            resume = Resume.objects.get(id=resume_id)
            serializer = ResumeDetailSerializer(resume)
            return Response(serializer.data)
        except Resume.DoesNotExist:
            logger.warning(f"Resume not found: {resume_id}")
            return Response(
                {'error': ERROR_MESSAGES['resume_not_found']},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error retrieving resume: {e}", exc_info=True)
            return Response(
                {'error': 'Failed to retrieve resume'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class InterviewStartView(APIView):
    """Start a new interview with validation."""
    throttle_classes = [InterviewRateThrottle]
    
    @transaction.atomic
    def post(self, request):
        """Start an interview session."""
        serializer = InterviewCreateSerializer(data=request.data)
        
        if not serializer.is_valid():
            logger.warning(f"Interview start validation failed: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        resume_id = serializer.validated_data['resume_id']
        experience_level = serializer.validated_data['experience_level']
        
        try:
            resume = Resume.objects.select_related().get(id=resume_id)
            
            # Check for existing active interview
            existing = Interview.objects.filter(
                resume=resume,
                status=Interview.Status.IN_PROGRESS
            ).first()
            
            if existing:
                logger.warning(f"Active interview already exists for resume: {resume_id}")
                return Response(
                    {'error': 'An interview is already in progress for this resume'},
                    status=status.HTTP_409_CONFLICT
                )
            
            # Create new interview
            interview = Interview.objects.create(
                resume=resume,
                experience_level=experience_level,
                status=Interview.Status.PENDING
            )
            
            # Start the interview
            interview.start()
            
            logger.info(f"Interview started: {interview.id}")
            
            response_serializer = InterviewSerializer(interview)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)
            
        except Resume.DoesNotExist:
            logger.warning(f"Resume not found for interview: {resume_id}")
            return Response(
                {'error': ERROR_MESSAGES['resume_not_found']},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Failed to start interview: {e}", exc_info=True)
            return Response(
                {'error': 'Failed to start interview', 'detail': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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


class CheatingReportView(APIView):
    """Report cheating events with atomic operations."""
    throttle_classes = [CheatingRateThrottle]
    
    @transaction.atomic
    def post(self, request):
        """Report cheating detection."""
        serializer = CheatingReportSerializer(data=request.data)
        
        if not serializer.is_valid():
            logger.warning(f"Cheating report validation failed: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            interview = Interview.objects.select_for_update().get(
                id=serializer.validated_data['interview_id']
            )
            
            confidence = serializer.validated_data['confidence']
            event_type = serializer.validated_data['event_type']
            details = serializer.validated_data.get('details', {})
            
            # Only record if confidence meets threshold
            if confidence < settings.CHEATING_CONFIDENCE_THRESHOLD:
                return Response({
                    'recorded': False,
                    'message': 'Confidence below threshold',
                    'strikes': interview.strikes,
                    'max_strikes': settings.MAX_STRIKES
                })
            
            # Check if already terminated
            if interview.status == Interview.Status.TERMINATED:
                return Response({
                    'recorded': False,
                    'message': 'Interview already terminated',
                    'strikes': interview.strikes,
                    'max_strikes': settings.MAX_STRIKES,
                    'terminated': True
                })
            
            # Add strike
            max_strikes_reached = interview.add_strike()
            strike_number = interview.strikes
            
            # Create cheating event (stored in DB for record keeping)
            CheatingEvent.objects.create(
                interview=interview,
                event_type=event_type,
                confidence=confidence,
                details=details,
                resulted_in_strike=True,
                strike_number=strike_number
            )
            
            # Add to summary
            if not interview.cheating_events_summary:
                interview.cheating_events_summary = []
            
            interview.cheating_events_summary.append({
                'strike_number': strike_number,
                'event_type': event_type,
                'confidence': confidence,
                'timestamp': timezone.now().isoformat(),
                'details': details
            })
            interview.save(update_fields=['cheating_events_summary'])
            
            logger.warning(f"Cheating event recorded: {event_type} for interview {interview.id}, strike {strike_number}/{settings.MAX_STRIKES}")
            
            # Terminate if max strikes reached
            if max_strikes_reached:
                # Build detailed termination reason
                termination_reason = (
                    f"Interview terminated after {strike_number} strikes (maximum: {settings.MAX_STRIKES}).\n\n"
                    f"Violations detected:\n"
                )
                for i, event_detail in enumerate(interview.cheating_events_summary, 1):
                    termination_reason += (
                        f"{i}. {event_detail['event_type'].replace('_', ' ').title()} "
                        f"(Confidence: {event_detail['confidence']:.0%})\n"
                    )
                
                termination_reason += (
                    f"\nThis session has been flagged for manual review. "
                    f"All detected violations were above the {settings.CHEATING_CONFIDENCE_THRESHOLD:.0%} confidence threshold."
                )
                
                interview.end(terminated=True, reason=termination_reason)
                logger.warning(f"Interview terminated due to cheating: {interview.id}")
                
                return Response({
                    'recorded': True,
                    'strikes': interview.strikes,
                    'max_strikes': settings.MAX_STRIKES,
                    'terminated': True,
                    'termination_reason': termination_reason,
                    'events': interview.cheating_events_summary,
                    'warning_message': self._get_warning_message(interview.strikes, True)
                })
            
            return Response({
                'recorded': True,
                'strikes': interview.strikes,
                'max_strikes': settings.MAX_STRIKES,
                'terminated': False,
                'warning_message': self._get_warning_message(interview.strikes, False)
            })
            
        except Interview.DoesNotExist:
            logger.warning(f"Interview not found for cheating report: {serializer.validated_data['interview_id']}")
            return Response(
                {'error': ERROR_MESSAGES['interview_not_found']},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Failed to process cheating report: {e}", exc_info=True)
            return Response(
                {'error': 'Failed to process cheating report', 'detail': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _get_warning_message(self, strikes, terminated):
        """Generate appropriate warning message."""
        max_strikes = settings.MAX_STRIKES
        if terminated:
            return "Interview terminated due to suspected cheating. This session has been flagged for review."
        elif strikes >= max_strikes:
            return "FINAL STRIKE: Interview will be terminated immediately on next violation."
        elif strikes == max_strikes - 1:
            return f"STRIKE {strikes}/{max_strikes}: One more violation will terminate your interview."
        else:
            return f"STRIKE {strikes}/{max_strikes}: Suspicious activity detected."

