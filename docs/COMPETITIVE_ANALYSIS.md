# Competitive analysis: AI interviewing & proctoring (as of 2026-10-05)

> **Read this first.** Almost every vendor claim below comes from marketing pages or third-party summaries; accuracy
> figures ("97 %", "99 % match with human proctors") are self-reported and unaudited. `(3P)` = third-party/unofficial
> source. `?` = not found or not stated. Treat the matrix as *publicly claimed*, not *verified*. Re-check before
> using it in sales material.

## 1. Who we compete with

Two overlapping markets:

* **Proctoring / assessment integrity vendors** (exam-native): Mercer Mettl, Honorlock, Proctorio, Meazure
  (ProctorU/Examity), Talview, plus code-assessment platforms with integrity layers (HackerRank, CodeSignal).
* **AI interviewer vendors** (interview-native): micro1, Alex (alex.com, ex-Apriora), Ribbon, Sapia.ai (text),
  Mercor, HireVue (video interviews + assessments), and interview-fraud detectors such as Sherlock and InterviewGuard.
* **Adversaries / tooling that shapes requirements:** Final Round AI, Cluely, Interview Coder and open-source
  clones (real-time copilots), voice cloning, deepfake and proxy candidates. Persona (ID + liveness) is the buy-vs-build
  comparator for identity.

## 2. Feature matrix (publicly claimed)

Y = claimed.

| Vendor | Face / multi-face | Gaze / head | ID + face match | Continuous identity | Liveness / deepfake | VM / remote / virtual cam | Secure browser | 2nd device / phone | Audio analysis | AI-assistant / overlay | Paste / keystroke / code similarity | Outcome model |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Mercer Mettl | Y | Y | Y | Y (claimed) | ? | Y (claimed) | Y | Y | Y (unique speakers) | ? | Y (typing anomalies) | Trust/suspicion score, timestamped flags, hybrid human review |
| HackerRank | Y ("webcam anomaly") | ? | ? | ? | ? | ? | Y (Secure Mode) | ? | ? | Y (screenshot analysis) | Y (paste tracking, AI plagiarism) | High/Medium integrity + session replay |
| CodeSignal | Y (human-reviewed) | n/s | Y (gov ID + selfie) | human review | ? | ? | ? | ? | recorded | logs its own "Cosmo" AI use | Y "Suspicion Score" | Human verification team |
| HireVue | periodic snapshots | ? | ? | Y (snapshots) | ? | ? | ? | ? | ? | ? | Tab/focus logs, answer similarity, paste disabled | Flags + human review; candidate disclosure |
| Honorlock | Y | ? | Y | ? | ? | Y | Y (BrowserGuard) | Y (phone detection, 2nd camera) | Y (phrase detection) | Y ("AI Blocking") | copy/paste disabled | AI alerts → live "Pop-In" proctor; open API |
| Proctorio | Y | Y | Y | ? | ? | ? | Chrome extension | ? | Y | ? | ? | AI-only suspicion level (3P: no built-in human loop) |
| Meazure (ProctorU/Examity) | Y | ? | Y (+ keystroke biometrics) | ? | ? | Y (n/s detail) | Y | ? | ? | ? | keystroke biometrics | Live+/Review+/Record+ tiers |
| Talview (Alvy) | Y | ? | Y | ? | content on deepfakes | ? | Y | Y (phone as 2nd camera) | ? | ? | ? | Agentic AI event record |
| Sherlock | Y | Y | ? | proxy-candidate detection | Y | Y (claimed) | n/a | ? | Y | Y (screen activity, clipboard) | clipboard | Unified classifier |
| micro1 ("Ava", 3P) | Y | Y | ? | ? | ? | ? | ? | ? | Y (second voice) | Y (extension/overlay) | Y | Integrity score, pass ≥ 70 % (3P) |
| Alex (alex.com) | ? | ? | Y (ID vs selfie) | ? | "multi-layer fraud detection" | ? | n/a | ? | ? | Y | Y (paste) | Post-session suspicious-behaviour report; 33+ ATS integrations |
| Sapia.ai | n/a (text) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | Y (AI-text detection, 98 % claimed / 1 % FPR) | text analysis | Real-time nudge → flag |
| Ribbon / Mercor | no public integrity features found | | | | | | | | | | | |

**Notes that matter for strategy**

* **Proctorio** is the cautionary tale: a published study reported face detection in ~78 % of the session for darker skin
  vs ~92 % for lighter, and the company litigated against critics. Bias in face detection is a *product* risk, not just a PR risk.
* **HireVue** (ACLU complaint, 2025), **Honorlock** and **ProctorU** (BIPA claims/investigations), **Eightfold** (FCRA class
  action, Jan 2026), **Mobley v. Workday** (collective certified May 2025) show where legal exposure lands: biometrics,
  accessibility and automated scoring.
* **Paradox / McHire** (June 2025: default password `123456`, up to 64 M applicant records exposed) shows that backend
  hygiene is a competitive differentiator in this space.
* **Overlay tools** (Cluely and clones) hide from screen capture with OS flags (`WDA_EXCLUDEFROMCAPTURE`,
  `NSWindowSharingNone`). Webcam, recording and lockdown browsers **cannot see them**; detection needs a client agent.
  Anyone claiming to catch them from video alone is overreaching.

## 3. White space we target

1. **Transparent evidence instead of an opaque score.** Per-signal records with raw metrics, thresholds, policy
   fingerprint and a hash-chained audit trail; a report a human can contest.
2. **Signals, not verdicts.** The engine never auto-rejects on soft evidence: everything ambiguous routes to `review`;
   abandonment never auto-fails; hard termination is reserved for policy-defined, high-confidence conditions and is a
   configurable choice per customer.
3. **Fairness and accessibility as first-class features.** Per-candidate calibration (no universal angle limits),
   named accommodations that switch off the specific rules a disability would trip, and an explicit non-goal list
   (no emotion/stress/"confidence" inference, no room scans).
4. **Interview-native signals in one API.** Most products are exam proctors. We correlate the *voice interview* with the
   video: audio-visual desync (voice with no matching lip motion), answer-latency signature, identity continuity across the
   conversation.
5. **API-first, integrator-neutral.** Tenant API keys, signed webhooks (`session.flagged/terminated/completed`), report and
   event endpoints, retention controls, no UI lock-in.
6. **Privacy by design.** No LLM or third-party call sees video; frames are analysed in memory and only single stills at
   violation time are stored (bounded, auto-expiring, SHA-256 bound into the audit chain); face templates are wiped when the
   session ends; one-call erasure; consent recorded before anything is scored.
7. **Offline resume intelligence that doubles as an integrity layer**: hidden-text/keyword-stuffing detection, prompt-injection
   detection (candidates embed instructions to manipulate AI screeners), duplicate/near-duplicate fingerprints, and a
   deterministic probe plan for the interviewer.

## 4. What we do **not** do (say this in sales calls)

| Gap | Why | Mitigation / roadmap |
|---|---|---|
| Invisible AI overlays & second device out of camera view | Not observable from webcam video | Optional client SDK/agent for window/process signals; behavioural signals (latency signature, read-aloud gaze pattern) as indirect evidence |
| Government-ID document verification | Needs document OCR/forensics + liveness | Reference-photo comparison is built in; integrate a specialist (e.g. Persona) via the `reference_image` hook |
| Deepfake / voice-clone *detection models* | No vetted permissive production-grade detector exists; generalisation is poor | `feed_integrity` catches replay/loop/static feeds; detector slot ready for a licensed model |
| Human proctor tier / review UI | Out of scope (backend-only) | `session.flagged` webhook + report + evidence API feed the customer's own review tooling |
| Independently audited accuracy | We have not run a bias/accuracy study | Ship a per-skin-tone/disability evaluation harness and publish results before claiming accuracy (see roadmap) |
| Reading-pattern & static-photo detectors are calibrated on synthetic and single-subject data only | No labelled corpus | Marked experimental; low default weight; calibrate on your own data before raising |

## 5. Positioning statement

> The proctoring engine for interview platforms that need to defend their decisions: explainable evidence, per-candidate
> calibration, accessibility built in, and an API that fits an existing hiring stack – with honest limits.

## 6. Roadmap (suggested order)

1. Reference **client SDK** (JS): frame capture, signed telemetry, preflight, fullscreen/visibility hooks.
2. **Evaluation harness**: synthetic and licensed datasets across skin tones, lighting, glasses, headscarves, mobility
   aids; report FPR per group; make thresholds data-driven. Publish it (no competitor does).
3. **Speaker diarisation / second voice** (permissive models only; check pyannote gated-weights terms) feeding `av_sync`.
4. **ID document + passive liveness** via partner integration.
5. Optional **client agent** for overlay/process detection for customers who can mandate it.
6. NYC LL144-style **bias-audit export** and EU AI Act technical documentation pack.

## Sources

Mettl: [blog](https://blog.mettl.com/ai-driven-online-proctoring-system-prevent-cheating/) ·
HackerRank: [Proctor Mode vs Secure Mode](https://www.hackerrank.com/writing/proctor-mode-vs-secure-mode-hackerrank-detects-chatgpt-ai-cheats-2025) ·
CodeSignal: [help](https://support.codesignal.com/hc/en-us/articles/360039872174-What-is-proctoring-and-how-does-it-work) ·
HireVue: [integrity](https://www.hirevue.com/blog/hiring/mitigating-cheating-enhancing-candidate-experience-ai-hiring), [ACLU complaint](https://www.hrdive.com/news/ai-intuit-hirevue-deaf-indigenous-employee-discrimination-aclu/743273/) ·
Honorlock: [proctoring](https://honorlock.com/proctoring/), [BIPA investigation](https://www.ahdootwolfson.com/blog/honorlock-facial-recognition-biometric-privacy-class-action-investigation/) ·
Proctorio: [bias study](https://racismandtechnology.center/2025/07/29/setting-the-record-straight-scientists-show-that-the-algorithm-that-proctorio-used-is-incredibly-biased-towards-people-with-a-darker-skin-colour/) ·
Meazure: [features](https://www.meazurelearning.com/exam-technology/proctoru-online-proctoring/compare-proctoring-features) ·
Talview: [Alvy](https://www.talview.com/en/alvy-ai-proctoring-agent) ·
Sherlock: [detection](https://www.sherlock.sh/interview-cheating-detection) ·
InterviewGuard: [product](https://www.interviewguard.io/product/ai-detection) ·
micro1: [paper](https://arxiv.org/pdf/2507.02869) ·
Alex: [product](https://www.alex.com/product/ai-interviewer) ·
Ribbon: [pricing](https://www.ribbon.ai/pricing) ·
Sapia: [AI detection](https://sapia.ai/resources/blog/keeping-interviews-real-with-next-gen-ai-detection/) ·
Persona: [candidate verification](https://www.prnewswire.com/news-releases/persona-launches-candidate-verification-to-stop-hiring-fraud-before-day-one-302711200.html) ·
Eightfold FCRA: [NatLawReview](https://natlawreview.com/article/ai-hiring-tools-and-consumer-reports-understanding-eightfold-litigation) ·
Paradox/McHire: [CSO](https://www.csoonline.com/article/4020919/mcdonalds-ai-hiring-tools-password-123456-exposes-data-of-64m-applicants.html) ·
Overlay mechanics: [aiseptor](https://aiseptor.com/detect-cluely) ·
Latency signature: [Fabric](https://fabrichq.ai/blogs/how-ai-interviews-detect-cheating-a-technical-deep-dive).
