from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/proctor/(?P<session_id>[0-9a-f-]{36})/$', consumers.ProctorConsumer.as_asgi()),
]
