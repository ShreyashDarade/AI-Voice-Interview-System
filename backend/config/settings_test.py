"""Settings for the test-suite: deterministic, offline, throw-away media directory."""
import os
import tempfile

os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production-0123456789')
os.environ['DEBUG'] = 'true'
os.environ.setdefault('ENVIRONMENT', 'test')
os.environ.setdefault('PROCTOR_REQUIRE_API_KEY', 'true')
os.environ.setdefault('GEMINI_API_KEY', 'test-key')
os.environ.setdefault('MEDIA_ROOT_OVERRIDE', tempfile.mkdtemp(prefix='proctor-test-media-'))
os.environ.pop('DATABASE_URL', None)
os.environ.pop('REDIS_URL', None)
os.environ.setdefault('RESUME_ISOLATED_PARSE', 'false')
for _k in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'OLLAMA_MODEL', 'OLLAMA_HOST', 'LLM_PROVIDER'):
    os.environ.pop(_k, None)             # tests must never reach a real model

from .settings import *  # noqa: E402,F401,F403

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
STATIC_ROOT = tempfile.mkdtemp(prefix='proctor-test-static-')
STORAGES = {**STORAGES, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}  # noqa: F405
