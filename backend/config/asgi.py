"""
ASGI config: HTTP + WebSocket.

WebSockets are wrapped in an Origin check (cross-site WebSocket hijacking guard)
on top of the signed candidate token each socket must present.
"""
import os

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator, OriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Initialise Django before importing anything that touches models.
django_asgi_app = get_asgi_application()

from django.conf import settings  # noqa: E402

from api.routing import websocket_urlpatterns  # noqa: E402

_ws = URLRouter(websocket_urlpatterns)
if settings.DEBUG:
    ws_app = _ws
elif settings.CORS_ALLOWED_ORIGINS:
    ws_app = OriginValidator(_ws, settings.CORS_ALLOWED_ORIGINS + [f'https://{h}' for h in settings.ALLOWED_HOSTS])
else:
    ws_app = AllowedHostsOriginValidator(_ws)

application = ProtocolTypeRouter({'http': django_asgi_app, 'websocket': ws_app})
