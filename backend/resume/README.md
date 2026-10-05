# `resume` - offline resume understanding engine

A pure-Python library (no Django import, no network, no LLM, no spaCy/NLTK/torch, no model downloads) that turns a
PDF / DOCX / TXT resume into structured data **plus** integrity signals and a deterministic interview *probe plan*.
Same input -> same output, always.

```python
from resume import parse_resume, ResumeParseError, UnsupportedFormat, MaliciousFile, ParseTimeout

result = parse_resume(path_or_bytes, filename=None, *, time_budget_s=20.0, ocr=True, default_region="US")
result.to_dict()                         # fully JSON-serialisable, schema_version "1.0"
result.integrity.flags                   # neutral review signals
result.probe_plan.to_prompt_context()    # injection-safe text for a voice interviewer's system prompt
```

## Pipeline

```
 bytes / path
     |
     v
 validate.py   magic-byte sniffing (PDF | DOCX | TXT; .doc/.rtf/images/binary -> UnsupportedFormat)
     |         size / page / zip-bomb limits, PDF active-content scan (never executes anything)
     v
 extract.py    PDF : PyMuPDF dict spans -> hidden-text filter -> column detection -> row merge ->
     |                header/footer removal -> bullet normalisation -> wrapped-line reflow + de-hyphenation
     |                (+ optional OCR for scanned pages)       => List[Line(font, bold, bbox, page ...)]
     |         DOCX: raw OOXML walk (body, tables, text boxes, headers/footers, hyperlinks, list styles, vanish/white runs)
     |         TXT : encoding sniff, rules/underlines, indentation reflow
     v
 privacy.py    drop DOB / gender / marital / nationality / religion / family / ID / photo   (-> redacted_fields)
     |
     v
 sections.py   heading lexicon + fuzzy (>=90) + formatting cues  => ordered Sections with line ranges
     |
     +--> contact.py     name (font size, heuristics, e-mail/linkedin fallback), e-mails, E.164 phones, location, links
     +--> skills.py      one trie-regex over data/skills.json (~976 skills) + context rules for ambiguous terms
     +--> experience.py  date ranges (dates.py) anchor entries; title/company/location from lexicons + separators
     +--> education.py   degree cues anchor entries; field / institution / years / GPA
     +--> extras.py      summary, projects, certifications, spoken languages
     v
 pipeline.py   interval-union total experience, per-skill months, seniority, confidences, warnings
     |
     +--> integrity.py   flags + risk score + SimHash fingerprint
     +--> plan.py        probe topics, verification claims, prompt context
     v
 ParsedResume (models.py)
```

Every sub-stage is wrapped: an unexpected exception becomes a `stage_failed:<stage>` warning and the rest of the
result is still returned (set `RESUME_STRICT=1` to re-raise - the test-suite does). Only file-level problems raise.
A cooperative `Deadline` is checked between stages and pages.

## Public API

| name | purpose |
|---|---|
| `parse_resume(path_or_bytes, filename=None, *, time_budget_s=20, ocr=True, default_region="US", limits=None)` | parse in-process |
| `parse_resume_isolated(..., hard_timeout_s=30, max_memory_mb=3072, include_text=False)` | parse in a throw-away **spawned** process; returns the `to_dict()` result; kills the worker on timeout (`ParseTimeout`), maps a crashed worker to `ResumeParseError` |
| `ParsedResume.to_dict(include_text=False)` / `.redacted_text` / `.text` | serialisation, PII-masked text, cleaned text |
| `build_probe_plan(parsed, experience_level=None, n_topics=8)` | deterministic probe plan (also attached as `parsed.probe_plan`) |
| `ProbePlan.to_prompt_context(max_chars=1500)` | compact, injection-safe prompt block |
| `similarity(fp_a, fp_b)` | 0..1 SimHash similarity of two `integrity.fingerprint`s (also accepts hex / int / dict) |
| `redact_pii(text)`, `sanitize_for_prompt(text, max_len)` | log-safe masking / prompt-safe single-line text |
| `Limits(max_bytes=10MB, max_pages=20, hard_max_pages=200, max_zip_members=1000, ...)` | ingestion limits |
| `SCHEMA_VERSION = "1.0"` | |

Exceptions (all subclass `ResumeParseError(ValueError)`; single-argument constructors so they pickle):
`UnsupportedFormat`, `FileTooLarge`, `TooManyPages`, `EncryptedFile`, `MaliciousFile`, `EmptyResume`, `ParseTimeout`.

**Django integration notes** - suggested HTTP mapping: `UnsupportedFormat` 415, `FileTooLarge`/`TooManyPages` 413,
`EncryptedFile`/`MaliciousFile`/`EmptyResume`/other `ResumeParseError` 422, `ParseTimeout` 504. For uploads from
untrusted users call `parse_resume_isolated` from a worker/Celery task (spawn is safe inside Django: no inherited DB
connections; ~0.3 s start-up cost). `to_dict()` of a typical resume is 15-40 KB of JSON. Nothing is ever written to disk.
`interview/resume_parser.py` is a thin shim that keeps the legacy `ResumeParser().parse(path)` dict.

## Result schema (abridged)

```
schema_version, source{format,pages,bytes,sha256,text_sha256,extraction_method,ocr_used},
contact{name,name_confidence,email,emails[],phone,phones[{e164,raw,region}],location,links[{url,type,label}]},
summary,
skills[{name,category,aliases_matched[],evidence,mentions,first_seen_in,evidence_sources[],experience_months,implied}],
soft_skills[...same...],
experience[{title,company,location,start 'YYYY-MM',end|null,is_current,duration_months,bullets[],skills[],confidence,
            employment_type full_time|internship|freelance|contract|part_time, date_text, date_precision}],
education[{degree_level,degree_raw,field,institution,start_year,end_year,gpa,gpa_scale,confidence}],
projects[{name,description,skills[],links[]}], certifications[{name,issuer,year}], languages[{name,proficiency}],
total_experience_months, total_experience_years, seniority_estimate fresher|junior|mid|senior|lead,
field_confidence{}, overall_confidence, warnings[], sections_detected[{name,heading,start_line,end_line}],
redacted_fields[], integrity{flags[{code,severity,message,details}],risk_score,fingerprint{simhash,text_sha256,shingles},metadata},
probe_plan{seniority,topics[{topic,reason_code,difficulty,evidence_snippets[],suggested_angles[],weight}],
           verification_claims[{claim,kind,source}],total_experience_months,integrity_notes[]}
```

Conventions: months are **inclusive** (`Jan 2020 - Mar 2020` = 3). Total experience is the **union** of all dated
intervals (overlaps and internships counted once; `Present`/`Current`/`Till date`/`Ongoing`/`Now` = today; future ends
clipped). Year-only dates are conservative (`2019 - 2021` = 24 months). Seniority: fresher < 12 months, junior < 36,
mid < 60, senior < 96, lead beyond; a lead/principal/manager/architect title bumps one level only when the months are
already >= 60 % of the next band. `skills[].experience_months` is the union of the intervals of roles whose text
evidences the skill (implied skills included). Warnings are short codes, e.g. `truncated_pages`,
`ocr_unavailable_scanned_pdf`, `hidden_text_removed`, `protected_attributes_removed`, `timeline_overlap_fulltime`,
`experience_section_not_found`, `no_contact_name`, `no_experience_found`, `text_truncated`, `line_truncated`,
`stage_failed:<stage>`.

## Safety policy (ingestion)

* Type from magic bytes; extension mismatch is only an `extension_mismatch` info flag.
* > 10 MB -> `FileTooLarge`; PDF > 200 pages -> `TooManyPages`; 20 < pages <= 200 -> first 20 parsed + `truncated_pages`.
* Password protected PDF -> `EncryptedFile`. Corrupt containers -> `ResumeParseError`/`UnsupportedFormat`.
* DOCX: member count, total/actual decompressed size, per-member ratio, path traversal; members are read through a
  bounded reader (declared sizes are not trusted); XML with a DTD/entity declaration is rejected, parser runs with
  `resolve_entities=False, no_network=True`.
* PDF: the non-stream part of the file and the PyMuPDF object table are scanned for `/JavaScript /JS /Launch
  /EmbeddedFile(s) /OpenAction /AA /AcroForm /XFA /RichMedia /SubmitForm`. **`/Launch`, or JavaScript together with
  `/OpenAction` or `/AA`, raises `MaliciousFile`.** Everything else is reported as `pdf_active_content`; nothing is executed.
* Cooperative `time_budget_s`; extracted text is capped (`Limits.max_text_chars`, `max_line_chars`).

## Integrity flag catalogue

| code | severity | notes |
|---|---|---|
| `hidden_text` | low (<20 chars) / medium / **high (> 50 chars)** | white/near-white (not on dark fill/image), < 3 pt, off-page; DOCX `vanish`, white, tiny. Excluded from all extraction, sample <= 200 chars in `details` |
| `prompt_injection_text` | high | instructions aimed at an AI screener (visible or hidden); never forwarded unsanitised |
| `keyword_stuffing` | medium / high | > 60 listed skills (> 100 = high), repeated-token runs, skills repeated >= 10x without sentences |
| `timeline_overlap_fulltime` | low | full-time roles at *different* companies overlapping > 3 months (also warning code) |
| `timeline_future_dates` | low (end) / medium (start) | future end is clipped to today |
| `start_after_end` | medium | role excluded from totals |
| `implausible_experience` | medium | role starts long before the earliest listed education ended, or total exceeds the time available (no age is ever computed) |
| `employment_gap` | info | > 12 months between roles / since the last role |
| `skill_without_evidence` | info / low | skills only in a skills list or summary |
| `too_many_jobs_short_tenure` | info / low | >= 4 (>= 6) full-time roles under 12 months |
| `duplicate_content` | info / low | same bullet in several roles |
| `contact_missing` | info / low / medium | no phone / no e-mail / neither |
| `template_text` | low / medium | placeholders such as "Your Name", lorem ipsum |
| `pdf_active_content` | info .. high | see policy above |
| `metadata_anomaly` | low / medium | creation/modification dates in the future or reversed (producer/creator reported, never judged) |
| `non_printable_chars` | info / medium | garbled fonts / encodings |
| `experience_claim_mismatch` | low | "10+ years of experience" vs dated roles |
| `extension_mismatch`, `truncated_pages` | info | |

`risk_score` = 1 - prod(1 - w) with w = 0.45 / 0.20 / 0.07 / 0 for high / medium / low / info. Flags are signals for review,
phrased neutrally; many have innocent explanations. **Protected attributes** (date of birth, age, gender, marital
status, nationality, religion, caste, ethnicity, family names, passport/visa/ID numbers, photo) are dropped before any
analysis and never appear in the output; `redacted_fields` lists the categories seen. Nothing is inferred from names.

`integrity.fingerprint`: `text_sha256` (exact, normalised text) and a 64-bit SimHash over normalised word 3-shingles;
`similarity()` = `1 - hamming/64` (near duplicates > 0.9, unrelated resumes ~0.5).

## Probe plan

`reason_code` in `claimed_expert_no_evidence, recent_primary_skill, gap_period, career_switch, short_tenure,
leadership_claim, project_depth, education_fundamentals, skill_breadth_vs_depth, integrity_flag_verification`.
Difficulty = f(per-skill months, seniority) (freshers capped at medium, seniors floored at medium). The best topic of every
reason is kept first, then the rest by weight. `verification_claims` are numbers the candidate asserts (team size, %,
money, scale, multipliers). `to_prompt_context()` is single-line, control-char free, backtick free, strips
role/template markers, replaces instruction-like phrases with `[removed]`, caps every embedded snippet (<= 90 chars)
and the whole block (`max_chars`).

## Skill taxonomy - how to extend

`data/skills.json` (lazy-loaded with `importlib.resources`, cached): `{"weak_surfaces": [...], "skills": [...]}`. A skill:

```json
{"name": "Kubernetes", "category": "cloud_devops", "aliases": ["k8s", "kubectl"], "implies": ["Docker"]}
{"name": "Go", "category": "programming_language", "aliases": ["golang"], "ambiguous": true, "case_sensitive": true,
 "requires_context": ["goroutine", "grpc", "backend"]}
```

* Matching is case-insensitive, token-bounded; spaces and hyphens in a form are interchangeable; use the exact written
  form of symbols (`c++`, `.net`, `node.js`, `ci/cd`). Each surface form must belong to one skill (the skill whose *name*
  is the surface owns it).
* `ambiguous`: its name (single word <= 10 chars) and any alias <= 3 chars need context. Append `?` to an alias (`"spark?"`)
  to make just that alias ambiguous. Letters-only surfaces of <= 2 chars are always ambiguous. `weak_surfaces` lists common
  English words that are only accepted as items of a skills list/section.
* Context = skills section, a delimited list with >= 2 other recognised skills, or a `requires_context` word within +-1 line.
* `case_sensitive`: surfaces <= 4 chars (`AI`, `SRE`, `iOS`) must be written in canonical/upper case outside skill lists.
* `implies` creates `implied: true` skills (React -> JavaScript) that inherit evidence and months.
* `category: "soft_skill"` goes to `soft_skills`. After editing run `pytest -p no:django resume/tests/test_skills.py`
  (it checks integrity: unique names, valid `implies`, size).

## Limitations (honest list)

* Heuristic parsing: unusual layouts (3+ columns, text in images, heavily graphical templates, timeline layouts with
  dates in a separate column) can still mis-order text or mis-assign title/company. Confidence values are heuristics, not
  calibrated probabilities.
* Title/company assignment relies on lexicons; unusual job titles without a known title word are classified by position.
* Hidden-text detection covers white / near-white / tiny / off-page text and DOCX `vanish`; it does **not** detect text
  hidden behind images or other shapes, clipping paths, zero-opacity text or PDF render-mode-3 text (which legitimate OCR'd
  PDFs also use). White text on a coloured box is treated as visible.
* OCR needs `pytesseract` + the `tesseract` binary (not installed by default); without it scanned PDFs raise `EmptyResume`
  (or yield the `ocr_unavailable_scanned_pdf` warning when some text exists).
* English-centric (month names, headings, degree names, skill aliases); Indian/US/UK conventions are best covered.
* Photo detection is a size/position heuristic on page-1 images (DOCX: inline picture size).
* The taxonomy is curated, not exhaustive; unknown tools are simply not recognised.
* Legacy `.doc`, RTF, ODT, images are rejected, not converted.
* PyMuPDF does the PDF parsing in-process: use `parse_resume_isolated` for untrusted uploads.
* Integrity flags are signals, not proof of anything; the plausibility checks compare role dates with education dates only.

## Tests

```
cd backend && python -m pytest -p no:django resume -q
```

`resume/pytest.ini` makes this directory the pytest rootdir so the Django-oriented `backend/conftest.py` is not loaded.
All fixtures (PDF/DOCX/TXT, scanned, hidden text, encrypted, zip bombs, injections) are generated in code.
