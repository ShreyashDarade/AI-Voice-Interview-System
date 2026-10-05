# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

# libegl1/libgles2/libgl1: MediaPipe 1.0 + OpenCV load these at runtime.
# tesseract-ocr is optional (scanned resumes); remove if you do not need OCR.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libegl1 libgles2 libgl1 libglib2.0-0 tesseract-ocr \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

WORKDIR /app
COPY backend/requirements.txt backend/requirements-ocr.txt ./
RUN pip install -r requirements-ocr.txt

COPY backend/ ./
RUN mkdir -p models media logs staticfiles && chown -R app:app /app
USER app

# Download + checksum-verify the vision models into the image (set SKIP_MODELS=1 to mount them instead).
ARG SKIP_MODELS=0
RUN if [ "$SKIP_MODELS" != "1" ]; then python manage.py fetch_proctor_models; fi
RUN SECRET_KEY=build DEBUG=false python manage.py collectstatic --noinput

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/').status==200 else 1)"
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
