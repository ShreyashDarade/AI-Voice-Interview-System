"""
Production-ready Django settings for AI Interviewer.
Security-hardened, performance-optimized, fully configured.
"""
import os
import sys
import warnings
import logging.config
from pathlib import Path

# Suppress TensorFlow warnings
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
warnings.filterwarnings('ignore', category=FutureWarning, module='keras')
warnings.filterwarnings('ignore', category=DeprecationWarning, module='keras')

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
DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

# Allowed hosts
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# Security middleware settings
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

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

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
        'CONFIG': {
            'capacity': 1000,  # Maximum messages per channel
            'expiry': 60,  # Message expiry in seconds
        }
    }
}

# =============================================================================
# DATABASE CONFIGURATION
# =============================================================================

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'timeout': 20,  # Prevent locks
            'check_same_thread': False,  # Allow multi-threaded access
        },
        'CONN_MAX_AGE': 0,  # Don't pool SQLite connections
    }
}

# Database optimization for SQLite
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
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

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
        'cheating': '500/hour' if DEBUG else '50/hour',
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
# ANTI-CHEATING CONFIGURATION
# =============================================================================

MAX_STRIKES = int(os.getenv('MAX_STRIKES', '2'))
CHEATING_CONFIDENCE_THRESHOLD = float(os.getenv('CHEATING_CONFIDENCE_THRESHOLD', '0.6'))
CHEATING_CHECK_INTERVAL = float(os.getenv('CHEATING_CHECK_INTERVAL', '1.0'))
CHEATING_TAB_SWITCH_ENABLED = os.getenv('CHEATING_TAB_SWITCH_ENABLED', 'True').lower() == 'true'
CHEATING_FACE_DETECTION_ENABLED = os.getenv('CHEATING_FACE_DETECTION_ENABLED', 'True').lower() == 'true'

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
