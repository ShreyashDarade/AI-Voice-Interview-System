"""
WebSocket URL routing.
"""
from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/interview/(?P<interview_id>[0-9a-f-]+)/$', consumers.InterviewConsumer.as_asgi()),
]
