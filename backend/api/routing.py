"""WebSocket URL routing."""
from django.urls import re_path

from proctoring.routing import websocket_urlpatterns as proctoring_ws

from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/interview/(?P<interview_id>[0-9a-f-]{36})/$', consumers.InterviewConsumer.as_asgi()),
    *proctoring_ws,
]
