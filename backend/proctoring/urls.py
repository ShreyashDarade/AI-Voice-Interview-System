from django.urls import path

from . import views as v

urlpatterns = [
    path('policies/', v.PolicyCatalogView.as_view(), name='proctor-policies'),
    path('capabilities/', v.CapabilitiesView.as_view(), name='proctor-capabilities'),
    path('sessions/', v.SessionCollectionView.as_view(), name='proctor-sessions'),
    path('sessions/<uuid:session_id>/', v.SessionDetailView.as_view(), name='proctor-session'),
    path('sessions/<uuid:session_id>/report/', v.SessionReportView.as_view(), name='proctor-report'),
    path('sessions/<uuid:session_id>/events/', v.SessionEventsView.as_view(), name='proctor-events'),
    path('sessions/<uuid:session_id>/terminate/', v.SessionTerminateView.as_view(), name='proctor-terminate'),
    path('sessions/<uuid:session_id>/evidence/<uuid:evidence_id>/', v.EvidenceDownloadView.as_view(),
         name='proctor-evidence'),
    # candidate-facing
    path('sessions/<uuid:session_id>/consent/', v.ConsentView.as_view(), name='proctor-consent'),
    path('sessions/<uuid:session_id>/preflight/', v.PreflightView.as_view(), name='proctor-preflight'),
    path('sessions/<uuid:session_id>/start/', v.StartView.as_view(), name='proctor-start'),
    path('sessions/<uuid:session_id>/complete/', v.CompleteView.as_view(), name='proctor-complete'),
]
