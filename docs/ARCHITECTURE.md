# Architecture

This is a **backend-only** product: an AI voice interviewer plus an API-first proctoring engine. There is no
UI, admin console or review dashboard in scope; integrators consume REST, WebSocket and signed webhooks.

Design rule: **framework first.** Detection logic is a plugin; policy is data; the verdict is a deterministic,
replayable function of persisted evidence. Adding a cheating signal means writing one class.

```
 candidate browser (any client)                                        integrator backend (ATS, …)
   │  JPEG frames @ ~2 Hz  │ signed telemetry batches │ mic audio            │ API key
   ▼                       ▼                          ▼                      ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ transport      proctoring.consumers.ProctorMixin  (ProctorConsumer, InterviewConsumer)    │
│                token auth · size/rate limits · back-pressure · signed-batch verification  │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ SessionRuntime (per session, in-process)                                                  │
│   VisionExtractor ──► features ──► Baseline ──► attention ──► FrameDetectors ─┐           │
│   (shared, pooled)    faces·pose·gaze  per-candidate                          ├─► Signals │
│                       objects·embedding  calibration     EventDetectors ──────┘           │
│                                                            (telemetry · VAD · turns)      │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ proctoring.services.apply_signals   (one DB transaction, session row locked)              │
│   RiskEngine rebuilt from persisted events ─► ingest (cooldowns, grace, caps) ─► evaluate │
│   ─► hash-chained ProctorEvent + Evidence still ─► state/verdict ─► webhook outbox        │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

## Why the engine is rebuilt from the database

Only *temporal* detector state (how long has this face been missing?) lives in memory. Everything that can
change a verdict is persisted in the same transaction as the decision. A worker restart, a reconnect to a
different process, or an HTTP call that lands on another worker cannot lose or corrupt evidence; at worst a
detector re-warms for a few seconds. It also makes every verdict reproducible: replay the events under the
stored policy and you get the same score.

## Concepts

| Concept | Where | Notes |
|---|---|---|
| `Signal` | `framework/types.py` | One observation: kind, category, confidence, details. *Evidence, not a verdict.* Keyword-only. |
| `FrameDetector` / `EventDetector` | `framework/detector.py` | Per-session plugin classes registered with `@register`. A detector may be both (`av_sync`). |
| `FeatureExtractor` | `framework/detector.py`, `vision/engine.py` | Computes expensive shared features once per frame. Process-wide singleton owning the models. |
| `Baseline` | `vision/baseline.py` | Learns the candidate's own neutral pose in the first seconds; all gaze/pose rules measure deviation from it. |
| `Policy` / `Rule` | `framework/policy.py` | All thresholds, weights, cooldowns, caps, detector sets. Presets `lenient`/`standard`/`strict`, sparse per-session overrides, accommodations. Fingerprinted and stored with the session. |
| `RiskEngine` | `framework/scoring.py` | Cooldowns, grace period, per-category caps, exponential decay (live score) vs cumulative score (verdict), sticky escalation, strikes. |
| `Pipeline` | `framework/pipeline.py` | extractor → baseline → detectors with failure isolation and per-detector latency stats. |
| Hash chain | `framework/audit.py` | `hash = sha256(prev ‖ canonical_json(record))` on every event; edits, deletions, reordering and tail truncation are detected by `verify_chain`. |
| Tokens | `framework/tokens.py` | Compact HMAC tokens (no JWT algorithm negotiation): `candidate` scope bound to one session; per-session telemetry key. |

## Detector catalogue

Every detector below is registered in `proctoring/detectors/` and enabled/disabled/tuned by policy.
The *kinds* are what the risk engine scores; every kind has a rule in `framework/policy.py` (enforced by a test).

| Detector | Family | Emits | How it works (no LLM) |
|---|---|---|---|
| `face_presence` | frame | `face_missing`, `multiple_faces`, `face_out_of_frame` | MediaPipe FaceLandmarker face count; sustained-duration state machines with flicker tolerance; secondary face must be ≥12 % of primary area; a motionless second face (poster) lowers confidence; a covered camera is *not* "missing". |
| `video_quality` | frame | `camera_blocked`, `poor_video_quality` | Brightness/contrast/sharpness (Laplacian variance) on the frame. |
| `gaze` | frame | `gaze_off_screen`, `looking_down` | Head pose (transformation matrix) + eye direction (blendshapes) measured **relative to the candidate's baseline**; sustained ≥ N s; head turned while eyes compensate is not flagged. |
| `head_pose` | frame | `head_turned_away` | Yaw deviation from baseline, sustained. |
| `reading_pattern` | frame | `reading_pattern` | **Experimental.** Sawtooth (slow sweep + fast return) in the horizontal eye signal with a still head, ≥4 cycles with regular period. |
| `periodic_glance` | frame | `periodic_glance` | Regular (CV < 0.2) or very frequent short glances off-screen. |
| `objects` | frame | `phone_detected`, `reference_material`, `additional_person` | EfficientDet-Lite0 (COCO): phone / book / laptop / tv / extra person bodies; k-of-n frame voting; min box size and score. |
| `identity` | frame | `identity_changed`, `identity_mismatch` | SFace embeddings of near-frontal, eyes-open, single-face crops; enrol 5, reject inconsistent enrolment (two people), flag after 3 consecutive low similarities; optional comparison with an integrator-supplied reference photo (only the embedding is kept, wiped at session end). |
| `feed_integrity` | frame | `frozen_feed`, `static_image_suspected`, `feed_loop_suspected`, `frame_timing_anomaly` | Exact duplicate frames; a face that never blinks/moves for 45 s (photo held up); near-exact repeat of an earlier span of video while the scene is live (pre-recorded loop); non-monotonic client clock. |
| `av_sync` | frame + event | `av_desync` | Server-side voice activity with no matching mouth motion for a frontal, visible face (audio injected via virtual cable / TTS, or someone else speaking). |
| `response_latency` | event | `latency_signature` | Metronomic delay before every answer (relaying questions to an assistant); needs ≥ 8 turns. |
| `browser_telemetry` | event | `tab_hidden`, `window_blur`, `fullscreen_exit`, `clipboard_paste`, `clipboard_copy`, `context_menu`, `devtools_open`, `multiple_displays`, `virtual_camera`, `camera_changed`, `blocked_shortcut`, `remote_access_suspected`, `client_integrity` | Client-reported context. **Untrusted by design**; corroboration only. Virtual-camera labels are classified strong (OBS, ManyCam, …) vs weak (phone-as-webcam apps, which are legitimate). |
| `client_integrity` | event | `client_integrity`, `client_silent`, `device_changed`, `concurrent_session` | Bad signatures, replayed/gapped sequence numbers, UA/IP changes, heartbeat loss. |
| `connectivity` | event | `disconnected`, `excessive_reconnects` | Gap length and reconnect count (policy-limited). |

### Writing a new detector

```python
from proctoring.framework.detector import FrameDetector, FrameContext, register
from proctoring.framework.types import Signal, Category, Severity, Source

@register
class HandOverMouth(FrameDetector):
    name = "hand_over_mouth"
    emits = ("hand_over_mouth",)
    requires = frozenset({"primary"})          # skipped when the feature is absent
    def process(self, ctx: FrameContext):
        ...
        return [Signal(kind="hand_over_mouth", category=Category.BEHAVIOR, severity=Severity.LOW,
                       confidence=0.7, source=Source.VISION, details={...}, ts=ctx.frame.ts)]
```
Then add a `Rule` for the kind in `BASE_RULES` (weight, cooldown, strike?, hard action?) and list the detector in the
preset's `detectors`. `tests/test_docs.py` fails until the catalogue above mentions it.

## Risk model

* `points = rule.weight × confidence`; a kind is counted at most once per `cooldown_s` (a phone on the desk
  is one incident per 30 s, not 60 per minute).
* **Grace period** (default 20 s after start) mutes soft signals while the camera and baseline settle; tampering and
  identity signals always count.
* **Category caps** stop one noisy sensor from reaching termination alone.
* **Live score** decays with a half-life (transient glances fade); **cumulative score** does not (you cannot wait
  an incident out) and feeds the end-of-session verdict.
* **Actions**: `WARN` → `FLAG` → `TERMINATE`, sticky (never de-escalate). Thresholds on the live score; strikes
  (rules marked `strike`) terminate at `max_strikes` when the policy says so; individual rules can force a minimum
  action (e.g. `identity_changed` → `TERMINATE` under `strict`).
* **Verdict**: `fail` (terminated), `review` (flagged, cumulative score ≥ `review_at`, or any HIGH+ severity event),
  else `clear`. Abandonment (`expired`) always goes to `review`, never `fail`.
* **Candidate messages** are generic and actionable ("Please remove other devices from view"); they never name a
  detector or threshold.

### Presets

| | lenient | standard | strict |
|---|---|---|---|
| rule weight scale | ×0.6 | ×1.0 | ×1.25 |
| warn / flag / terminate (live score) | 30 / 55 / 100 | 20 / 40 / 85 | 15 / 30 / 70 |
| strikes terminate | no (flags only) | at 3 | at 2 |
| grace period | 30 s | 20 s | 15 s |
| face missing / second face (sustained) | 8 s / 4 s | 4 s / 2 s | 2.5 s / 1.5 s |
| virtual camera, identity change, feed loop | flag | flag | **terminate** |

### Accommodations (first-class, audited)

`relaxed_gaze`, `assistive_technology`, `reference_notes`, `shared_space` switch off the specific detectors/rules a
disability or circumstance would otherwise trip, without weakening integrity-critical checks (identity, tampering,
multiple faces stay on unless the accommodation explicitly covers them). They are stored on the session and shown in the report.

## Transport

* **Candidate WebSocket** `/ws/proctor/<session_id>/` (proctoring only) or `/ws/interview/<interview_id>/` (voice + proctoring).
  Auth: `Sec-WebSocket-Protocol: proctor.v1, <candidate_token>` (preferred, keeps the token out of URLs/logs) or `?token=`.
* **Frames**: binary = `!Id` header (uint32 seq, float64 client time) + JPEG (≤ 300 KB, ≤ 4096² px checked from the
  SOF marker *before* decoding). On the voice socket prefix with `PXF1`. Frames arriving faster than
  `MAX_FRAME_RATE_HZ` or while the previous frame is still being analysed are **dropped** (no queue ⇒ never lags real time).
* **Telemetry**: `{"type":"events","seq":n,"payload":"<json array>","sig":"<hmac>"}`; sequence numbers must increase,
  signatures bind session + seq + body; unknown kinds and oversized events are discarded.
* **Server → client**: `ready`, `status` (faces, calibrated), `notice` (warning with strike counts), `terminated`, `error`.

## Persistence

`ProctorSession` (state machine `created → preflight → active ⇄ paused → completed | terminated | expired`),
`ProctorEvent` (append-only, hash-chained), `Evidence` (single JPEG stills at violation time, bounded count and
interval, SHA-256 bound into the chain, auto-expiring), `Tenant` (API key stored as SHA-256), `WebhookDelivery`
(transactional outbox, at-least-once, exponential backoff, HMAC-signed, SSRF-guarded).

Operations: `proctor_watchdog` (pause silent sessions after the heartbeat timeout, expire abandoned ones, deliver
webhooks), `proctor_purge` (retention), `fetch_proctor_models`, `create_tenant`.

## Optional LLM layer (`backend/llm/`)

Policy: **video and integrity decisions never involve an LLM** (a test fails if `proctoring` imports `llm`). Text-only
helpers may use one *if you configure a provider* – otherwise everything falls back to deterministic code.

| Provider | Enabled by | Notes |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` (model default `claude-sonnet-5-5`, override `LLM_MODEL`) | Messages API over `httpx`, no SDK dependency |
| OpenAI / any OpenAI-compatible server | `OPENAI_API_KEY` + `LLM_MODEL` (`OPENAI_BASE_URL` for vLLM, LM Studio, …) | We do not guess a default model id |
| Ollama (local) | `OLLAMA_MODEL` (+ `OLLAMA_HOST`) | Nothing leaves the machine |

`LLM_ENABLED=false` is a kill switch; `LLM_PROVIDER` forces a choice. Uses: tailored interview questions
(`interview/question_generator.py`, templates as fallback) and `POST /api/interview/<id>/evaluate/` – a transcript summary
with evidence snippets that is **decision support, flagged `human_review_required`, never a hire/reject verdict**, and
receives no integrity data. All candidate text is redacted (email/phone/URL), control/bidi characters stripped,
injection phrasing neutralised, wrapped as `<data>`; replies are parsed, schema-checked and clipped; failures fall back to
rules. The live voice interviewer itself is Gemini Live (`interview/gemini_live.py`); swapping the voice stack is out of scope.

## Resume engine

See [`backend/resume/README.md`](../backend/resume/README.md). Offline and deterministic: layout-aware PDF/DOCX
extraction, section segmentation, taxonomy-based skills, month-granular experience with interval union, integrity
flags (hidden text, keyword stuffing, timeline anomalies, prompt-injection text), a similarity fingerprint, and a
deterministic interview probe plan that is injected (sanitised) into the voice interviewer's system prompt.

## Scaling notes

* CPU-bound: ~20–70 ms per analysed frame (FaceLandmarker ≈ 17 ms warm on a laptop CPU; EfficientDet every 2nd frame;
  SFace every 4 s). At 2 fps that is ≈ 4–14 % of one core per candidate. `PROCTOR_POOL_SIZE` model instances per process.
* Run N Daphne processes behind a load balancer with `REDIS_URL` and PostgreSQL. A candidate's socket is sticky to one
  process for its lifetime; correctness never depends on stickiness (see above).
* GPU is not required.
