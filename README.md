# AI Voice Interview + Proctoring Backend

A **backend-only** (REST + WebSocket + webhooks) platform that runs an AI voice interview and proctors it:

* **Proctoring engine** – plugin *framework* (detectors → signals → explainable risk engine → tamper-evident log),
  on-device video analysis with **no LLM**: face presence/extra people, per-candidate-calibrated gaze & head pose,
  phones/books/second persons, identity continuity, frozen/static/looped feeds, audio-visual desync, answer-latency
  signature, signed browser telemetry. Presets `lenient | standard | strict`, accommodations, consent, evidence stills,
  retention/erasure, signed webhooks, full session report.
* **Resume engine** – offline, rule-based, layout-aware parsing with integrity flags and a deterministic interview
  probe plan (`backend/resume/`).
* **Voice interviewer** – Gemini Live via the official `google-genai` SDK, with session resumption.
* **Optional LLM helpers** (text only; Anthropic / OpenAI-compatible / local Ollama; off unless configured; never in
  proctoring).

Read: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`docs/API.md`](docs/API.md) ·
[`docs/COMPETITIVE_ANALYSIS.md`](docs/COMPETITIVE_ANALYSIS.md) · [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md) ·
[`backend/resume/README.md`](backend/resume/README.md)

> `frontend/` is the legacy demo client. It predates the token/consent/proctoring protocol and **does not work against
> this backend until updated** (it opens the voice socket without a candidate token). The backend contract is in `docs/API.md`.

## Quick start

Requires **Python ≥ 3.12** (Django 6.1, NumPy 2.5; tested on 3.13).

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
# MediaPipe 1.0 / OpenCV need system libs on Linux:  sudo apt install libegl1 libgles2 libgl1
cp ../.env.example .env                    # set GEMINI_API_KEY for voice
python manage.py migrate
python manage.py fetch_proctor_models      # ~50 MB, SHA-256 verified, never committed
python manage.py create_tenant "My ATS"    # prints the API key once
daphne -b 127.0.0.1 -p 8000 config.asgi:application
python manage.py proctor_watchdog          # separate process: pause/expire sessions, deliver webhooks
```

Docker: `docker compose up --build` (Postgres + Redis + API + watchdog + purge).

## Tests

```bash
cd backend && pytest -q                                   # ~200 tests, offline
# real-model integration tests (head-pose sign convention, calibration, duplicate/static feeds, WS with real video):
PROCTOR_TEST_PORTRAIT=/path/to/frontal_face.jpg pytest -q proctoring/tests/test_vision_models.py proctoring/tests/test_ws.py
```

## Layout

```
backend/
  proctoring/   framework/ (types, detector API, policy, scoring, pipeline, tokens, audit)  vision/  detectors/
                services.py consumers.py views.py webhooks.py models.py management/commands/
  resume/       offline resume engine            llm/         optional text-only LLM helpers
  interview/    Gemini Live client, audio front-end (NumPy), question generator
  api/          REST (resume, interview, health), voice WebSocket
  core/         Resume / Interview models        config/      settings (+ settings_test)
docs/           architecture · API · competitors · compliance
```

## Status & honest limits

* Heuristic detectors (`reading_pattern`, `static_image_suspected`, gaze thresholds) are validated on synthetic data and a
  single portrait only. Calibrate on your own labelled data and run a bias evaluation across skin tones, lighting,
  glasses and head coverings **before** relying on them. Signals are evidence for human review, not proof.
* Overlay copilots (Cluely-style) and off-camera second devices cannot be detected from video; see
  `docs/COMPETITIVE_ANALYSIS.md` §4.
* The Gemini Live path was ported to the current SDK and unit-tested with a fake client; it has **not** been run against the
  live API from this repo. Verify `GEMINI_MODEL` against Google's current Live model list.
* Model weights licences need a legal review before commercial launch (`docs/COMPLIANCE.md`).
