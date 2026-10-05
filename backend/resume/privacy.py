"""Privacy helpers: protected-attribute dropping, PII redaction, prompt-injection guards.

Protected attributes
    The engine **never extracts and never outputs** date of birth, age, gender,
    marital status, nationality/citizenship, religion, caste, ethnicity, family
    member names, passport/visa details or the candidate's photo.  Lines (or
    ``Label: value`` segments of a line) that carry such data are dropped
    *before* any other stage runs, so they can influence neither parsing nor
    scoring, and the categories found are reported in ``redacted_fields`` (the
    values themselves are not retained).  Nothing in this package infers
    protected attributes from names, photos or other signals.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Set, Tuple

from .extract import Line

# label -> redacted_fields name.  STRONG labels may be followed by ':', '-', a tab or 2+ spaces;
# WEAK labels (which could be a company / job word) require a colon.
_STRONG: List[Tuple[str, str]] = [
    (r"date\s+of\s+birth|d\.?\s?o\.?\s?b\.?|birth\s*date|birthday|place\s+of\s+birth|birth\s*place", "date_of_birth"),
    (r"gender", "gender"),
    (r"marital\s+status|marriage\s+status", "marital_status"),
    (r"nationality|citizenship", "nationality"),
    (r"religion", "religion"),
    (r"ethnicity", "ethnicity"),
    (r"father'?s?\s+name|mother'?s?\s+name|spouse(?:'s)?\s+name|husband'?s?\s+name|wife'?s?\s+name|parents?'?\s+name|guardian'?s?\s+name", "family_details"),
    (r"passport(?:\s+(?:no|number|#))?\.?|visa\s+status|work\s+permit|aadhaar(?:\s+(?:no|number))?|aadhar(?:\s+(?:no|number))?|pan\s+(?:no|number)|ssn|social\s+security(?:\s+number)?", "identity_documents"),
    (r"blood\s+group|physically\s+challenged|disability\s+status", "physical_attributes"),
]
_WEAK: List[Tuple[str, str]] = [
    (r"age", "age"),
    (r"sex", "gender"),
    (r"born(?:\s+on)?", "date_of_birth"),
    (r"marital", "marital_status"),
    (r"nationality|citizen", "nationality"),
    (r"caste|category", "caste"),
    (r"race", "ethnicity"),
    (r"father|mother|spouse", "family_details"),
    (r"height|weight", "physical_attributes"),
    (r"visa", "identity_documents"),
]
_LABEL_RE = [(re.compile(rf"^\s*(?:{pat})\s*(?:[:\-–—]|\s{{2,}}|\t)\s*\S", re.I), name) for pat, name in _STRONG]
_LABEL_RE += [(re.compile(rf"^\s*(?:{pat})\s*:\s*\S", re.I), name) for pat, name in _WEAK]
# labels where the colon is optional because the value is unmistakable
_NO_COLON_RE = [
    (re.compile(r"^\s*(?:date\s+of\s+birth|d\.o\.b\.?|dob|born)\s+[0-9A-Za-z]", re.I), "date_of_birth"),
]
_STANDALONE_VALUES = {
    "male": "gender", "female": "gender", "married": "marital_status", "unmarried": "marital_status",
    "single": "marital_status", "divorced": "marital_status", "widowed": "marital_status",
}
_SEG_SPLIT = re.compile(r"\s*(?:\||\t|•|·|;)\s*")
_AGE_PHRASE_RE = re.compile(r"\b\d{2}\s*[- ]?\s*(?:years?|yrs?)[\s-]*old\b", re.I)


def _segment_redaction(seg: str) -> str:
    """Return field name if the segment is a protected ``Label: value``; else ''."""
    for rx, name in _LABEL_RE:
        if rx.match(seg):
            # "Age: 28" fine; but "Age" alone is not a segment match (needs a value)
            return name
    for rx, name in _NO_COLON_RE:
        if rx.match(seg):
            return name
    if seg.strip().lower() in _STANDALONE_VALUES:
        return _STANDALONE_VALUES[seg.strip().lower()]
    if _AGE_PHRASE_RE.search(seg) and len(seg.split()) <= 6:
        return "age"
    return ""


def strip_protected(lines: List[Line]) -> Tuple[List[Line], List[str]]:
    """Drop protected-attribute lines / segments. Returns ``(clean_lines, redacted_fields)``."""
    found: List[str] = []
    out: List[Line] = []
    for ln in lines:
        text = ln.text
        # fast path
        low = text.lower()
        if not any(k in low for k in (
            "birth", "dob", "d.o.b", "born", "age", "gender", "sex", "marital", "nationality", "citizen",
            "religion", "caste", "community", "ethnic", "race", "father", "mother", "spouse", "husband", "wife",
            "passport", "visa", "permit", "aadhaar", "aadhar", "pan ", "ssn", "social security", "height", "weight",
            "blood", "disab", "challenged", "health", "male", "married", "single", "divorced", "widowed", "guardian",
            "parent", "category", "faith",
        )):
            out.append(ln)
            continue
        segs = _SEG_SPLIT.split(text)
        kept: List[str] = []
        dropped = False
        for seg in segs:
            name = _segment_redaction(seg)
            if name:
                dropped = True
                if name not in found:
                    found.append(name)
            elif seg.strip():
                kept.append(seg.strip())
        if not dropped:
            out.append(ln)
        elif kept:
            ln.text = " | ".join(kept)
            out.append(ln)
    return out, found


# --------------------------------------------------------------------------- PII redaction

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+|\b(?:linkedin|github|gitlab|stackoverflow|kaggle|leetcode|twitter|x)\.com/[^\s<>\"')\]]+", re.I)
_PHONE = re.compile(r"(?<![\w])(?:\+?\d[\d\s().-]{7,}\d)(?![\w])")


def redact_pii(text: str) -> str:
    """Mask e-mail addresses, URLs and phone numbers (for logs / analytics)."""
    if not text:
        return ""
    text = _EMAIL.sub("[EMAIL]", text)
    text = _URL.sub("[URL]", text)

    def phone(m: "re.Match[str]") -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) < 9 or len(digits) > 15:
            return m.group(0)
        # leave obvious year ranges alone ("2019 - 2021")
        if re.fullmatch(r"(?:19|20)\d{2}\D+(?:19|20)\d{2}", m.group(0).strip()):
            return m.group(0)
        return "[PHONE]"

    return _PHONE.sub(phone, text)


# --------------------------------------------------------------------------- prompt injection

INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|preceding)\s+(?:instructions?|prompts?|messages?|context|rules)",
    r"disregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|preceding)\s+(?:instructions?|prompts?|messages?|context|rules)?",
    r"forget\s+(?:all\s+|everything\s+|your\s+)?(?:previous|prior|above|earlier)?\s*(?:instructions?|prompts?|rules)",
    r"override\s+(?:your\s+|the\s+|all\s+)?(?:instructions?|system\s+prompt|rules)",
    r"you\s+are\s+now\s+(?:a|an|the|in)\b",
    r"act\s+as\s+(?:a|an|the)\s+(?:different|new|unrestricted)",
    r"(?:^|\n|\s)system\s*:\s",
    r"(?:^|\n|\s)assistant\s*:\s",
    r"\[/?INST\]",
    r"<<\s*/?SYS\s*>>",
    r"<\|\s*(?:im_start|im_end|system|user|assistant|endoftext)\s*\|>",
    r"###\s*(?:instruction|system|response)",
    r"as\s+an\s+ai\s+(?:language\s+)?model",
    r"(?:rate|score|rank|grade|evaluate)\s+this\s+(?:candidate|resume|applicant|cv)\s+(?:as\s+)?(?:highly|10|high|excellent|the\s+best|top)",
    r"(?:recommend|hire|select|shortlist|advance)\s+this\s+(?:candidate|applicant)",
    r"(?:give|assign)\s+(?:this\s+)?(?:resume|candidate|applicant)\s+(?:a\s+)?(?:high|perfect|top|maximum)\s+score",
    r"(?:this\s+candidate\s+is\s+(?:an?\s+)?(?:exceptional|perfect|ideal|the\s+best))",
    r"do\s+not\s+(?:mention|reveal|disclose|flag)\s+(?:this|these|any)",
    r"reveal\s+(?:your\s+)?(?:system\s+prompt|instructions)",
    r"jailbreak|prompt\s+injection|\bDAN\s+mode\b",
    r"```",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.I)


def find_injection_phrases(text: str, limit: int = 5) -> List[str]:
    out: List[str] = []
    for m in _INJECTION_RE.finditer(text or ""):
        frag = re.sub(r"\s+", " ", m.group(0)).strip()[:60]
        if frag and frag not in out:
            out.append(frag)
        if len(out) >= limit:
            break
    return out


_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏ -‮⁠-⁤﻿]")
_ROLE_TOKENS = re.compile(r"(?i)\b(?:system|assistant|user|human|ai)\s*:")
_TEMPLATE_TOKENS = re.compile(r"(\{\{|\}\}|\$\{|<\||\|>|<<|>>|\[/?INST\]|<s>|</s>)")


def sanitize_for_prompt(text: str, max_len: int = 160) -> str:
    """Make resume-derived text safe to embed in an LLM system prompt.

    Strips control / zero-width chars, fenced code, template and role markers,
    neutralises instruction-like phrases (replaced by ``[removed]``) and caps the
    length.  The result is always a single line.
    """
    if not text:
        return ""
    t = _CTRL.sub(" ", str(text))
    t = t.replace("`", "'")
    t = _INJECTION_RE.sub(" [removed] ", t)
    t = _ROLE_TOKENS.sub(" [removed] ", t)
    t = _TEMPLATE_TOKENS.sub(" ", t)
    t = re.sub(r"[<>]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > max_len:
        t = t[: max_len - 1].rstrip() + "…"
    return t
