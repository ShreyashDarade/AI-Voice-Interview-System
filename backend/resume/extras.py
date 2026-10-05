"""Best-effort extraction of summary, projects, certifications and spoken languages."""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .dates import find_date_ranges, strip_ranges
from .experience import is_header_like
from .extract import Line
from .models import Certification, LanguageSkill, Project

_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+|\b(?:github|gitlab|bitbucket)\.com/[^\s<>\"')\]]+", re.I)
_META_LABEL = re.compile(
    r"^(?:tech(?:nologies|nology)?(?:\s+stack)?|tools?(?:\s+used)?|stack|built\s+with|skills?|environment|technologies\s+used|"
    r"github|link|links|demo|live|url|source|role|duration|team\s+size|description|domain|client)\s*[:\-–]",
    re.I,
)


# --------------------------------------------------------------------------- summary

def build_summary(lines: Sequence[Line], idxs: Sequence[int]) -> str:
    parts = [lines[i].text.strip() for i in idxs if lines[i].text.strip()]
    text = " ".join(parts)
    text = re.sub(r"\s+", " ", text)
    return text[:1200]


def fallback_summary(lines: Sequence[Line], header_idxs: Sequence[int]) -> str:
    for i in header_idxs:
        t = lines[i].text.strip()
        if len(t.split()) >= 15 and "@" not in t and not re.search(r"\d{5,}", t):
            return t[:1200]
    return ""


# --------------------------------------------------------------------------- projects

def _project_name(header: str) -> Tuple[str, str]:
    """Return ``(name, tech_hint)`` from a project header line."""
    rngs = find_date_ranges(header)
    h = strip_ranges(header, rngs) if rngs else header
    h = re.sub(r"\(\s*(?:link|github|demo|live|source|url)[^)]*\)", " ", h, flags=re.I)
    parts = [p.strip() for p in re.split(r"\s*(?:\||·|•|\t|\s[-–—]\s|\s—\s)\s*", h) if p.strip()]
    if not parts:
        return "", ""
    name = parts[0]
    tech = " ".join(parts[1:])
    m = re.match(r"^(.{3,60}?)\s*[:]\s*(.*)$", name)
    if m and len(m.group(2).split()) <= 6 and "," in m.group(2):
        name, tech = m.group(1), m.group(2) + " " + tech
    m2 = re.match(r"^(.{3,70}?)\s*\(([^)]{2,80})\)\s*$", name)
    if m2 and "," in m2.group(2):
        name, tech = m2.group(1), m2.group(2) + " " + tech
    return name.strip(" :,-–—"), tech


def parse_projects(
    region: Sequence[Tuple[int, Line]],
    names_for_lines: Callable[[int, int], List[str]],
) -> List[Project]:
    projects: List[Project] = []
    cur: Optional[dict] = None

    def flush() -> None:
        nonlocal cur
        if not cur:
            return
        name = cur["name"]
        if name:
            desc = " ".join(cur["body"])[:600]
            links = []
            for ln in cur["raw"]:
                for m in _URL.finditer(ln):
                    u = m.group(0).rstrip(".,;:)")
                    if u not in links:
                        links.append(u)
            skills = names_for_lines(cur["first"], cur["last"] + 1)
            projects.append(Project(name=name, description=desc, skills=skills, links=links[:5]))
        cur = None

    for gi, ln in region:
        t = ln.text.strip()
        if not t:
            continue
        if _META_LABEL.match(t):
            if cur:
                cur["raw"].append(t)
                cur["body"].append(t)
                cur["last"] = gi
            continue
        header = (not ln.is_bullet) and is_header_like(ln) and len(t.split()) <= 14
        if header and (cur is None or cur["body"]):
            flush()
            name, tech = _project_name(t)
            cur = {"name": name, "body": [], "raw": [t], "first": gi, "last": gi}
            if tech:
                cur["body"].append("Tech: " + tech.strip())
            continue
        if cur is None:
            continue
        cur["raw"].append(t)
        if header and not cur["body"]:
            cur["body"].append(t)         # subtitle line directly under the name
        else:
            cur["body"].append(t)
        cur["last"] = gi
    flush()
    return projects[:20]


# --------------------------------------------------------------------------- certifications

_ISSUERS = [
    "Amazon Web Services", "AWS", "Google Cloud", "Google", "Microsoft", "Oracle", "Cisco", "CompTIA", "PMI",
    "Coursera", "Udemy", "edX", "Udacity", "LinkedIn Learning", "Scrum Alliance", "Scrum.org", "Red Hat", "HashiCorp",
    "Salesforce", "IBM", "Meta", "Databricks", "Snowflake", "Pluralsight", "DeepLearning.AI", "Stanford", "NPTEL",
    "Infosys", "Kubernetes Foundation", "CNCF", "Linux Foundation", "ISC2", "(ISC)²", "ISACA", "EC-Council", "Offensive Security",
    "Tableau", "SAS", "Cloudera", "Mongodb", "MongoDB", "Docker", "GitHub", "Atlassian", "ServiceNow", "SAP", "Adobe",
    "Harvard", "MIT", "Duke", "Johns Hopkins", "University of Michigan", "HackerRank", "Kaggle", "freeCodeCamp", "Simplilearn",
    "Great Learning", "upGrad", "Codecademy", "DataCamp", "Fast.ai", "NVIDIA", "Intel", "Palo Alto Networks", "Fortinet",
    "AXELOS", "PeopleCert", "Six Sigma", "ASQ", "IIBA", "Project Management Institute", "Azure", "Alteryx", "UiPath",
]
_ISSUER_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(i) for i in sorted(_ISSUERS, key=len, reverse=True)) + r")(?![A-Za-z])")


def parse_certifications(region: Sequence[Tuple[int, Line]]) -> List[Certification]:
    items: List[str] = []
    for _, ln in region:
        t = ln.text.strip()
        if not t or len(t) < 4:
            continue
        if items and not ln.is_bullet and t[0].islower() and len(items[-1]) < 140:
            items[-1] += " " + t
            continue
        items.append(t)
    out: List[Certification] = []
    for t in items:
        if len(t) > 220:
            continue
        year = None
        rngs = find_date_ranges(t)
        if rngs:
            r = rngs[-1]
            y = (r.end if r.end is not None else r.start)
            year = y // 12 if y is not None else None
            base = strip_ranges(t, rngs)
        else:
            base = t
        issuer = ""
        ms = list(_ISSUER_RE.finditer(base))
        if ms:
            issuer = max((x.group(1) for x in ms), key=len)
        else:
            m2 = re.search(r"(?:\bby\b|\bfrom\b|\bissued by\b|\bvia\b)\s+([A-Z][\w&.\- ]{2,40})$", base) or re.search(r"[|–—-]\s*([A-Z][\w&.\- ]{2,40})$", base)
            if m2:
                issuer = m2.group(1).strip()
        name = base
        if issuer:
            name = re.sub(r"\(\s*" + re.escape(issuer) + r"\s*\)", " ", name)
            name = re.sub(r"(?i)\b(?:issued\s+)?(?:by|from|via)\s+" + re.escape(issuer) + r"\b", " ", name)
            if name.strip().lower() != issuer.lower():
                name = re.sub(r"(?<![A-Za-z])" + re.escape(issuer) + r"(?![A-Za-z])", " ", name, count=1) if re.sub(r"\s", "", name) != re.sub(r"\s", "", issuer) else name
        name = re.sub(r"\s{2,}", " ", name).strip(" ,.-–—|:()[]")
        if not name or len(name) < 3:
            name = base.strip(" ,.-–—|:()")
        if len(name) < 3:
            continue
        out.append(Certification(name=name[:140], issuer=issuer, year=year))
    return out[:30]


# --------------------------------------------------------------------------- languages

_LANGS = {
    "english", "hindi", "spanish", "french", "german", "italian", "portuguese", "russian", "chinese", "mandarin",
    "cantonese", "japanese", "korean", "arabic", "bengali", "bangla", "tamil", "telugu", "marathi", "gujarati",
    "kannada", "malayalam", "punjabi", "urdu", "odia", "oriya", "assamese", "nepali", "sinhala", "dutch", "swedish",
    "norwegian", "danish", "finnish", "polish", "czech", "greek", "turkish", "hebrew", "persian", "farsi", "thai",
    "vietnamese", "indonesian", "malay", "tagalog", "filipino", "swahili", "afrikaans", "ukrainian", "romanian",
    "hungarian", "catalan", "sanskrit", "bulgarian", "serbian", "croatian", "slovak", "slovenian", "lithuanian", "latvian", "estonian",
    "icelandic", "albanian", "macedonian", "armenian", "georgian", "azerbaijani", "kazakh", "uzbek", "mongolian", "burmese", "khmer", "lao",
    "amharic", "somali", "hausa", "yoruba", "igbo", "zulu", "xhosa", "pashto", "kurdish", "tibetan", "irish", "welsh", "basque", "galician",
    "bosnian", "belarusian", "maltese", "luxembourgish", "dari", "uyghur", "swedish", "konkani", "sindhi", "kashmiri", "maithili", "bhojpuri", "tulu", "latin",
}
_PROF = [
    ("native", r"native|mother\s+tongue|first\s+language"), ("bilingual", r"bilingual"),
    ("fluent", r"fluent|fluency|full\s+professional|c[12]\b|advanced|proficient|professional"),
    ("conversational", r"conversational|intermediate|b[12]\b|working\s+proficiency|limited\s+working"),
    ("basic", r"basic|beginner|elementary|a[12]\b|limited|read(?:ing)?\s+only|learning"),
]


def parse_languages(region: Sequence[Tuple[int, Line]]) -> List[LanguageSkill]:
    out: List[LanguageSkill] = []
    seen = set()
    for _, ln in region:
        for part in re.split(r"\s*[,;|•·]\s*|\t|\s{2,}", ln.text):
            part = part.strip()
            if not part:
                continue
            m = re.match(r"^([A-Za-z]+)\s*(?:[\-–:(]\s*(.*?)\)?)?$", part)
            if m:
                name = m.group(1)
                prof_txt = (m.group(2) or "").lower()
            else:
                toks = part.split()
                name = toks[0]
                prof_txt = " ".join(toks[1:]).lower()
            if name.lower() not in _LANGS or name.lower() in seen:
                continue
            seen.add(name.lower())
            prof = ""
            for label, rx in _PROF:
                if re.search(rx, prof_txt):
                    prof = label
                    break
            out.append(LanguageSkill(name=name.capitalize(), proficiency=prof))
    return out
