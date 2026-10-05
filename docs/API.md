# API reference

Base path `/api/`. JSON unless noted. **Integrator** endpoints use `Authorization: Bearer px_<prefix>_<secret>`
(create with `manage.py create_tenant`). **Candidate** endpoints use the short-lived `candidate_token` returned at
session creation. In local `DEBUG` an API key can be made optional with `PROCTOR_REQUIRE_API_KEY=false`.

## Flow

```
integrator                         backend                                   candidate browser
  POST /resume/upload/ ──────────►  parse (offline), integrity flags, probe plan
  POST /interview/start/ ────────►  Interview(PENDING) + ProctorSession + credentials
        (candidate_token, telemetry_key, ws paths) ───────────────────────────►
                                                         POST …/consent/        (accepted: true)
                                                         WS  /ws/proctor/<id>/  (frames → calibration, preflight status)
                                                         POST …/preflight/      (optional single-frame check)
                                                         POST …/start/          → interview IN_PROGRESS
                                                         WS  /ws/interview/<interview_id>/  (voice + frames + telemetry)
  ◄── webhooks: session.created/started/flagged/terminated/completed/expired
  GET …/report/ · …/events/ · …/evidence/<id>/
```

## Integrator endpoints

| Method & path | Purpose |
|---|---|
| `POST /resume/upload/` (multipart `file`) | Parse a resume (PDF/DOCX/TXT). Returns parsed fields, `integrity`, `probe_plan`. |
| `POST /interview/start/` `{resume_id, experience_level, policy?, policy_overrides?, accommodations?, external_ref?}` | Create the interview **and** its proctoring session. Returns `proctor_session_id`, `candidate_token`, `telemetry_key`, `frame_rate_hz`, `ws_path`, `voice_ws_path`. |
| `POST /proctor/sessions/` `{interview_id, policy?, policy_overrides?, accommodations?, external_ref?, reference_image?}` | Proctoring only (bring your own interview). `reference_image` (multipart) → face embedding for identity comparison; the photo is discarded. |
| `GET /proctor/sessions/<id>/` | Live state: `state`, `verdict`, `risk_score`, `strikes`, `max_action`. |
| `GET /proctor/sessions/<id>/report/` | Full explainable report (`proctor-report/1`): policy fingerprint, consent, category scores, top reasons, timeline, evidence list, **audit-chain verification**, coverage, limitations. |
| `GET /proctor/sessions/<id>/events/?after=<seq>&limit=` | Paginated, hash-linked event log. |
| `GET /proctor/sessions/<id>/evidence/<eid>/` | JPEG still (`X-Content-SHA256` header; `Cache-Control: private, no-store`). |
| `POST /proctor/sessions/<id>/terminate/` `{reason}` | Manual termination → `terminated` / `fail`. |
| `DELETE /proctor/sessions/<id>/` | Erase an ended session (events, evidence, biometrics). |
| `GET /proctor/policies/` | Presets and accommodations, with every rule weight. |
| `GET /proctor/capabilities/` | Which vision capabilities are loaded; detector → signal kinds. |

`policy_overrides` (sparse): `warn_at, flag_at, terminate_at, review_at, fail_at, max_strikes, strikes_terminate,
grace_period_s, calibration_s, heartbeat_timeout_s, disconnect_grace_s, max_reconnects, frame_rate_hz, half_life_s,
require_identity_reference, max_evidence_per_session, detector_config{detector:{…}}, disable_detectors[], rule_weights{kind:weight}`.

## Candidate endpoints (`Bearer <candidate_token>`)

`POST /proctor/sessions/<id>/consent/ {accepted:true, version?}` → `POST …/preflight/` (multipart `frame`: returns
`{ready, issues[], instructions[], metrics}`) → `POST …/start/` → … → `POST …/complete/`.
`start` returns 412 `consent_required` / `reference_required` when preconditions are unmet.

## WebSocket (candidate)

`/ws/proctor/<session_id>/` (proctoring only) or `/ws/interview/<interview_id>/` (voice + proctoring; requires an
`active` session, else close code 4012). Authenticate with `Sec-WebSocket-Protocol: proctor.v1, <candidate_token>`.

Client → server

* **Frame** (binary): `struct.pack('!Id', seq, client_time_s) + jpeg`. On the voice socket prefix `b'PXF1'`.
  Send at `frame_rate_hz`; anything faster or sent while the previous frame is still processing is dropped.
* **Telemetry** (text): `{"type":"events","seq":n,"payload":"<JSON array>","sig":"<b64url HMAC>"}`.
  `payload` items: `{"kind","ts","data"}`, `kind` ∈ `visibility{hidden}`, `focus{focused}`, `fullscreen{active}`,
  `clipboard{action,length}`, `contextmenu`, `devtools{open}`, `displays{count,extended}`,
  `camera{event:selected|changed|ended,label}`, `shortcut{combo}`, `env{webdriver,display_count,remote_indicators}`.
  `sig = b64url(HMAC-SHA256(telemetry_key, "<session_id>:<seq>:" + payload))`; `seq` strictly increasing.
* `{"type":"heartbeat"}` every ~10 s · `{"type":"complete"}` (alias `end_interview` on the voice socket).

Server → client: `ready`, `status {faces, calibrated, …}`, `notice {level, message, strikes, max_strikes}`,
`terminated {message}` (then close 4003), `error {code}`. Close codes: 4001 bad token, 4004 unknown session,
4009 replaced by a newer connection, 4010 session ended, 4012 not started, 4429 capacity.

Reconnects within `disconnect_grace_s` resume the same session (state, baseline and identity template are restored;
the gap is recorded as a `disconnected` signal). Beyond it the watchdog expires the session to `review`.

## Webhooks

`POST` to the tenant `webhook_url` (https only; private/loopback/link-local targets are refused), at-least-once with
exponential backoff (max `PROCTOR_WEBHOOK_MAX_ATTEMPTS`). Headers: `X-Proctor-Event`, `X-Proctor-Delivery`,
`X-Proctor-Signature: t=<unix>,v1=<hex>` where `v1 = HMAC-SHA256(webhook_secret, "<t>." + raw_body)`.
Reject if `|now − t| > 5 min`. De-duplicate by `X-Proctor-Delivery`.

```python
import hmac, hashlib, time
def verify(secret: str, header: str, body: bytes) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(parts["t"])) > 300: return False
    mac = hmac.new(secret.encode(), parts["t"].encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, parts["v1"])
```
Events: `session.created|started|flagged|completed|expired|terminated`; `data` carries `session_id, interview_id,
external_ref, state, verdict, risk_score, strikes, audit_chain_head` (anchor `audit_chain_head` in your own store for
non-repudiation).

## Signing telemetry in the browser

```js
async function sign(keyB64url, sessionId, seq, payload) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(keyB64url),
                                            {name:"HMAC", hash:"SHA-256"}, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${sessionId}:${seq}:${payload}`));
  return btoa(String.fromCharCode(...new Uint8Array(mac))).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,"");
}
```
> The telemetry key lives in the candidate's browser; signing stops forgery/replay by third parties, **not** a candidate
> who controls their own browser. Telemetry is therefore always corroboration, never sole proof.
