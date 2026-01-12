"""
URL routes for AI Interviewer API.
"""
from django.urls import path
from .views import (
    HealthCheckView,
    ResumeUploadView, ResumeDetailView,
    InterviewStartView, InterviewStatusView, InterviewEndView,
    CheatingReportView
)

urlpatterns = [
    # Health check
    path('health/', HealthCheckView.as_view(), name='health'),
    
    # Resume endpoints
    path('resume/upload/', ResumeUploadView.as_view(), name='resume-upload'),
    path('resume/<uuid:resume_id>/', ResumeDetailView.as_view(), name='resume-detail'),
    
    # Interview endpoints
    path('interview/start/', InterviewStartView.as_view(), name='interview-start'),
    path('interview/<uuid:interview_id>/status/', InterviewStatusView.as_view(), name='interview-status'),
    path('interview/<uuid:interview_id>/end/', InterviewEndView.as_view(), name='interview-end'),
    
    # Cheating detection
    path('cheating/report/', CheatingReportView.as_view(), name='cheating-report'),
]
