# Compliance & privacy design

> Engineering notes, **not legal advice**. Regulatory statements come from desk research on 2026-10-05; items marked
> ⚠ were not independently verified. Have counsel review before launch in each jurisdiction.

## What the system does with personal data

| Data | Stored? | Where / how long | Notes |
|---|---|---|---|
| Live video frames | **No** (analysed in memory) | – | Never forwarded to an LLM or third party. |
| Violation stills (JPEG ≤ 640 px) | Yes, bounded | `Evidence`, `PROCTOR_EVIDENCE_RETENTION_DAYS` (30) | ≤ `max_evidence_per_session` (40) and ≥ 10 s apart; SHA-256 bound into the audit chain; downloadable only by the owning tenant. |
| Face embedding of the candidate (identity template) | Session only | `detector_state['identity']` | **Wiped when the session ends** (complete / terminate / expire). |
| Embedding of an integrator-supplied reference photo | Session only | `reference_embedding` | The photo itself is discarded immediately; embedding wiped at session end. |
| Event log (signals, lifecycle) | Yes | `ProctorEvent`, `PROCTOR_SESSION_RETENTION_DAYS` (90) | Hash-chained. Contains numbers (confidence, durations), no images. |
| Consent (time, version, IP) | Yes | on the session | Required before the session can start. |
| Candidate text sent to an LLM (only if a provider is configured) | Redacted of email/phone/URLs; not stored by us beyond the result | Anthropic / OpenAI / your gateway, or **nothing leaves the host with Ollama** | Needs a processor agreement and disclosure in the consent text. Never video, never integrity data. Disable with `LLM_ENABLED=false`. |
| Resume text & parsed data | Yes | `Resume` | Protected attributes (DOB, gender, marital status, nationality, photo) are dropped by the resume engine. |

Erasure: `DELETE /api/proctor/sessions/<id>/` (after the session ended) hard-deletes events and evidence files;
`manage.py proctor_purge` enforces retention daily.

## Deliberate non-features

* **No emotion, stress, "confidence", personality or micro-expression inference.** The EU AI Act prohibits emotion
  recognition in workplace and education contexts (Art. 5(1)(f), in force since 2 Feb 2025). Blendshapes are used only
  for gaze/blink/mouth *geometry*, never to infer feelings.
* **No room scans / 360° environment sweeps** (a US court held a public university's room scan unconstitutional,
  *Ogletree v. Cleveland State*, 2022; ⚠ later appellate history unverified).
* **No automatic rejection from soft evidence.** Ambiguous cases route to human review; abandonment never fails a candidate.
* **No protected-attribute inference** from resumes or video.
* **No covert capture:** nothing is scored before consent + start.

## Requirements → where the code supports them

| Requirement | Support |
|---|---|
| **GDPR Art. 9** (face used for identification is special-category data) – explicit consent / legal basis, DPIA, EU residency | Consent gate (`consent_required`, versioned); identity checks are policy-optional and can be turned off per session (`disable_detectors: ["identity"]`); deploy in an EU region; DPIA checklist below. |
| **GDPR Art. 22** (solely automated decisions) | Soft evidence → `review`; only policy-configured hard conditions terminate; every termination is explainable (`report.summary.top_reasons`, timeline) and appealable by the integrator via the events/evidence API. |
| **GDPR Art. 17** erasure / retention | `DELETE` endpoint, `proctor_purge`, evidence TTL, biometric wipe. |
| **EU AI Act** (proctoring & employment screening are Annex III high-risk; logging, human oversight, bias testing, technical documentation) – ⚠ the "Digital Omnibus" reportedly moved the Annex III deadline to 2 Dec 2027; Art. 50 transparency unchanged | Tamper-evident event log; `review` verdict + webhooks for human oversight; policy fingerprint/version in every report for reproducibility; evaluation harness is on the roadmap (not done). |
| **Illinois BIPA** (notice, written consent, public retention schedule, destruction) | Consent record; retention schedule = this document + env vars; templates destroyed at session end. |
| **Illinois AI Video Interview Act** (notify, explain, consent *before* the interview, delete within 30 days on request) | Consent before start; erasure endpoint; the integrator must present the explanation text (backend returns `consent_version`). |
| **Illinois HB 3773 (from 1 Jan 2026)** – notice when AI influences employment decisions | Integrator responsibility; report supplies the factors. |
| **NYC Local Law 144** (annual independent bias audit, candidate notice) | Roadmap: bias-audit export. Until then an integrator must obtain its own audit. |
| **ADA / accommodations** | Named accommodations (`relaxed_gaze`, `assistive_technology`, `reference_notes`, `shared_space`) stored on the session and shown in the report. |
| **FCRA exposure** for scoring (Eightfold action) | We expose *evidence for review*, not a hiring score; keep it that way in downstream use. |
| **Security** | API keys stored as SHA-256 + constant-time compare; per-session scoped, short-lived HMAC candidate tokens; signed + sequence-numbered telemetry; tenant isolation (404 for foreign sessions); WS Origin validation; SSRF-guarded webhooks; frame size/rate limits and JPEG header checks before decode; no API key in URLs (Gemini key is sent by the SDK in a header). |

## Consent text the front end must show (minimum)

1. That video and audio are analysed *during* the interview by software to detect rule violations, and what is checked
   (face presence, gaze/head direction, objects such as phones, identity continuity, browser activity).
2. That **no emotion or personality inference** is performed.
3. What is stored (still images at flagged moments, event log), for how long, who can see it, and how to request deletion.
4. That a human reviews flagged sessions and that the candidate can contest a result.
5. How to request an accommodation *before* the interview.

## DPIA checklist (starting point)

- [ ] Lawful basis and consent wording per jurisdiction · [ ] Region/residency of DB, Redis and evidence storage
- [ ] Retention values set (`PROCTOR_*_RETENTION_DAYS`) and documented · [ ] Processor agreements (hosting, Gemini voice)
- [ ] Bias evaluation across skin tones, lighting, glasses, head coverings, mobility aids (**not yet provided by this repo**)
- [ ] Human-review procedure and candidate appeal path · [ ] Breach response · [ ] Model licence review (below)

## Model licences (verify before commercial launch)

| Model | Use | Licence | Caveat |
|---|---|---|---|
| MediaPipe `face_landmarker.task`, `efficientdet_lite0.tflite` | landmarks/pose, objects | Apache-2.0 terms (Google) | ⚠ confirm the bundle terms for your use. |
| OpenCV Zoo `SFace` | identity embeddings | Apache-2.0 | Training-data provenance has been questioned in opencv_zoo issues #313/#318 → legal review. |
| **Not used**: Ultralytics YOLO (AGPL-3.0, network use triggers copyleft), InsightFace pretrained weights (non-commercial) | – | – | Do not swap these in without a commercial licence. |
