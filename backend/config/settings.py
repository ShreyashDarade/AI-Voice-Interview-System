"""
Production-ready Django settings for AI Interviewer.
Security-hardened, performance-optimized, fully configured.
"""
import os
import sys
import logging.config
from pathlib import Path

# MediaPipe / TFLite are chatty on stderr
os.environ.setdefault('GLOG_minloglevel', '2')
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '2')

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Build paths
BASE_DIR = Path(__file__).resolve().parent.parent

# =============================================================================
# SECURITY SETTINGS
# =============================================================================

# Secret key - MUST be set in production
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    if os.getenv('ENVIRONMENT', 'development') == 'production':
        raise ValueError("SECRET_KEY must be set in production environment")
    import secrets
    SECRET_KEY = secrets.token_urlsafe(50)
    print("WARNING: Using generated SECRET_KEY for development only")

# Debug mode
def _env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


DEBUG = _env_bool('DEBUG', False)

# Allowed hosts
ALLOWED_HOSTS = [h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]

# Security middleware settings (SECURE_BROWSER_XSS_FILTER was removed in Django 4.0)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'same-origin'
if _env_bool('TRUST_PROXY_SSL_HEADER'):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# HTTPS settings (enable in production)
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# =============================================================================
# APPLICATION DEFINITION
# =============================================================================

INSTALLED_APPS = [
    'daphne',
    'channels',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'core',
    'proctoring',
    'api',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# =============================================================================
# CHANNELS CONFIGURATION
# =============================================================================

REDIS_URL = os.getenv('REDIS_URL', '')
if REDIS_URL:
    CHANNEL_LAYERS = {'default': {'BACKEND': 'channels_redis.core.RedisChannelLayer',
                                  'CONFIG': {'hosts': [REDIS_URL], 'capacity': 1000, 'expiry': 60}}}
else:
    # Single-process only (dev/tests). Set REDIS_URL for more than one worker.
    CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}

# =============================================================================
# DATABASE CONFIGURATION
# =============================================================================

if os.getenv('DATABASE_URL'):
    import dj_database_url
    DATABASES = {'default': dj_database_url.config(conn_max_age=60, conn_health_checks=True)}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'OPTIONS': {'timeout': 20},
        }
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================

LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO' if not DEBUG else 'DEBUG')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{levelname}] {asctime} {name} {module}:{lineno} - {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
        'simple': {
            'format': '[{levelname}] {message}',
            'style': '{',
        },
    },
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        },
        'require_debug_true': {
            '()': 'django.utils.log.RequireDebugTrue',
        },
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'stream': sys.stdout,
        },
        'file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': BASE_DIR / 'logs' / 'app.log',
            'maxBytes': 1024 * 1024 * 10,  # 10MB
            'backupCount': 5,
            'formatter': 'verbose',
        },
        'error_file': {
            'level': 'ERROR',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': BASE_DIR / 'logs' / 'error.log',
            'maxBytes': 1024 * 1024 * 10,  # 10MB
            'backupCount': 5,
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console', 'error_file'],
            'level': 'WARNING',
            'propagate': False,
        },
        'api': {
            'handlers': ['console', 'file', 'error_file'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'interview': {
            'handlers': ['console', 'file', 'error_file'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
    },
    'root': {
        'handlers': ['console'],
        'level': LOG_LEVEL,
    },
}

# Create logs directory
LOGS_DIR = BASE_DIR / 'logs'
LOGS_DIR.mkdir(exist_ok=True)

# =============================================================================
# PASSWORD VALIDATION
# =============================================================================

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# =============================================================================
# INTERNATIONALIZATION
# =============================================================================

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# =============================================================================
# STATIC & MEDIA FILES
# =============================================================================

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'
                    if not DEBUG else 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

MEDIA_URL = '/media/'
MEDIA_ROOT = Path(os.getenv('MEDIA_ROOT_OVERRIDE') or os.getenv('MEDIA_ROOT') or BASE_DIR / 'media')

# Create media directory
MEDIA_ROOT.mkdir(exist_ok=True)

# =============================================================================
# CORS CONFIGURATION
# =============================================================================

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:5173').split(',')
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = True

# =============================================================================
# REST FRAMEWORK CONFIGURATION
# =============================================================================

REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',
        'rest_framework.parsers.FormParser',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        # More lenient in development, strict in production
        'anon': '1000/hour' if DEBUG else '100/hour',
        'upload': '100/hour' if DEBUG else '10/hour',
        'interview': '200/hour' if DEBUG else '20/hour',
        'proctor_create': '600/hour' if DEBUG else '120/hour',
        'proctor_preflight': '600/hour' if DEBUG else '120/hour',
    },
    'EXCEPTION_HANDLER': 'api.exceptions.custom_exception_handler',
}

# =============================================================================
# GEMINI API CONFIGURATION
# =============================================================================

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
if not GEMINI_API_KEY:
    print("WARNING: GEMINI_API_KEY not set - interview functionality will not work")

GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash-native-audio-preview-12-2025')
GEMINI_VOICE_NAME = os.getenv('GEMINI_VOICE_NAME', 'Aoede')

# Construct WebSocket URL securely (API key added at connection time, not stored)
GEMINI_WS_BASE_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"

# Gemini connection settings
GEMINI_CONNECTION_TIMEOUT = int(os.getenv('GEMINI_CONNECTION_TIMEOUT', '30'))
GEMINI_MAX_RETRIES = int(os.getenv('GEMINI_MAX_RETRIES', '3'))
GEMINI_RETRY_DELAY = float(os.getenv('GEMINI_RETRY_DELAY', '2.0'))

# =============================================================================
# INTERVIEW CONFIGURATION
# =============================================================================

INTERVIEW_INTRODUCTION_REQUIRED = True
INTERVIEW_ENABLE_CRITICAL_MODE = True
INTERVIEW_MIN_ANSWER_LENGTH = 50
INTERVIEW_MAX_DURATION_MINUTES = int(os.getenv('INTERVIEW_MAX_DURATION_MINUTES', '60'))
INTERVIEW_SESSION_TIMEOUT_MINUTES = int(os.getenv('INTERVIEW_SESSION_TIMEOUT_MINUTES', '90'))

# =============================================================================
# AUDIO PROCESSING CONFIGURATION
# =============================================================================

VAD_ENERGY_THRESHOLD = float(os.getenv('VAD_ENERGY_THRESHOLD', '0.025'))
VAD_ZCR_THRESHOLD = float(os.getenv('VAD_ZCR_THRESHOLD', '0.15'))
VAD_SPEECH_FRAMES = int(os.getenv('VAD_SPEECH_FRAMES', '5'))
VAD_SILENCE_FRAMES = int(os.getenv('VAD_SILENCE_FRAMES', '12'))
NOISE_SUPPRESSION_ENABLED = os.getenv('NOISE_SUPPRESSION_ENABLED', 'True').lower() == 'true'

# Audio buffer limits
AUDIO_MAX_BUFFER_SIZE_MB = int(os.getenv('AUDIO_MAX_BUFFER_SIZE_MB', '50'))
AUDIO_CLEANUP_INTERVAL_SECONDS = int(os.getenv('AUDIO_CLEANUP_INTERVAL_SECONDS', '30'))

# =============================================================================
# PROCTORING (see proctoring/ and docs/ARCHITECTURE.md)
# =============================================================================

PROCTOR = {
    # model weights live here (fetch with `manage.py fetch_proctor_models`)
    'MODEL_DIR': Path(os.getenv('PROCTOR_MODEL_DIR', BASE_DIR / 'models')),
    'POOL_SIZE': int(os.getenv('PROCTOR_POOL_SIZE', '2')),       # concurrent model instances per process
    'ENABLE_OBJECTS': _env_bool('PROCTOR_ENABLE_OBJECTS', True),
    'ENABLE_IDENTITY': _env_bool('PROCTOR_ENABLE_IDENTITY', True),
    'DEFAULT_POLICY': os.getenv('PROCTOR_DEFAULT_POLICY', 'standard'),
    # candidate credentials
    'TOKEN_SECRET': os.getenv('PROCTOR_TOKEN_SECRET', SECRET_KEY),
    'TOKEN_TTL_S': int(os.getenv('PROCTOR_TOKEN_TTL_S', str(4 * 3600))),
    # API-key auth for integrators (server-to-server). Required unless DEBUG.
    'REQUIRE_API_KEY': _env_bool('PROCTOR_REQUIRE_API_KEY', not DEBUG),
    # transport limits
    'MAX_FRAME_BYTES': int(os.getenv('PROCTOR_MAX_FRAME_BYTES', '300000')),
    'MAX_FRAME_RATE_HZ': float(os.getenv('PROCTOR_MAX_FRAME_RATE_HZ', '6')),
    'MAX_WS_MESSAGE_BYTES': int(os.getenv('PROCTOR_MAX_WS_MESSAGE_BYTES', '400000')),
    # retention / privacy
    'EVIDENCE_RETENTION_DAYS': int(os.getenv('PROCTOR_EVIDENCE_RETENTION_DAYS', '30')),
    'SESSION_RETENTION_DAYS': int(os.getenv('PROCTOR_SESSION_RETENTION_DAYS', '90')),
    'CONSENT_VERSION': os.getenv('PROCTOR_CONSENT_VERSION', '2026-10'),
    # webhooks
    'WEBHOOK_TIMEOUT_S': float(os.getenv('PROCTOR_WEBHOOK_TIMEOUT_S', '8')),
    'WEBHOOK_MAX_ATTEMPTS': int(os.getenv('PROCTOR_WEBHOOK_MAX_ATTEMPTS', '8')),
}

# =============================================================================
# FILE UPLOAD CONFIGURATION
# =============================================================================

FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB

# Allowed file types
ALLOWED_RESUME_EXTENSIONS = ['pdf', 'docx', 'txt']
ALLOWED_RESUME_CONTENT_TYPES = [
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'text/plain',
]

# =============================================================================
# PERFORMANCE & RESOURCE LIMITS
# =============================================================================

# Limit concurrent WebSocket connections
MAX_CONCURRENT_INTERVIEWS = int(os.getenv('MAX_CONCURRENT_INTERVIEWS', '50'))

# Database query timeout
DATABASE_QUERY_TIMEOUT_SECONDS = int(os.getenv('DATABASE_QUERY_TIMEOUT_SECONDS', '30'))

# Memory limits for processing
MAX_RESUME_SIZE_MB = int(os.getenv('MAX_RESUME_SIZE_MB', '10'))
# Parse uploads in a throw-away subprocess with a hard timeout (a hostile PDF cannot hang or crash a web worker)
RESUME_ISOLATED_PARSE = _env_bool('RESUME_ISOLATED_PARSE', True)
MAX_AUDIO_CHUNK_SIZE_KB = int(os.getenv('MAX_AUDIO_CHUNK_SIZE_KB', '256'))

# =============================================================================
# HEALTH CHECK CONFIGURATION
# =============================================================================

HEALTH_CHECK_ENABLED = True
HEALTH_CHECK_DATABASE = True
HEALTH_CHECK_GEMINI_API = os.getenv('HEALTH_CHECK_GEMINI_API', 'True').lower() == 'true'

# =============================================================================
# DATA RETENTION & CLEANUP
# =============================================================================

# Auto-cleanup old data
DATA_RETENTION_DAYS = int(os.getenv('DATA_RETENTION_DAYS', '90'))
CLEANUP_ENABLED = os.getenv('CLEANUP_ENABLED', 'True').lower() == 'true'

# =============================================================================
# MONITORING & METRICS
# =============================================================================

ENABLE_METRICS = os.getenv('ENABLE_METRICS', 'True').lower() == 'true'
METRICS_INTERVAL_SECONDS = int(os.getenv('METRICS_INTERVAL_SECONDS', '60'))

# Application version
APP_VERSION = os.getenv('APP_VERSION', '1.0.0')
ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')
