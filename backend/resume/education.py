"""Education extraction: entries are anchored on degree cues, never on bare years."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from .dates import find_date_ranges, strip_ranges
from .extract import Line
from .models import EducationEntry

_OF = (r"(?:\s+of\s+(?:Science|Arts|Technology|Engineering|Commerce|Business(?:\s+Administration)?|Computer\s+Applications?|"
       r"Fine\s+Arts|Laws?|Architecture|Education|Pharmacy|Management|Medicine|Philosophy|Public\s+Health|Applied\s+Science|"
       r"Information\s+Technology|Computer\s+Science|Social\s+Work|Music|Nursing|Accountancy|Economics|Surgery|Dental\s+Surgery))?")

# (level, case-sensitive abbreviation regexes, case-insensitive word regexes)
_DEGREE_SPECS: List[Tuple[str, List[str], List[str]]] = [
    ("doctorate", [r"Ph\.?\s?D\.?", r"D\.?\s?Phil\.?"], [r"doctorate", r"doctor\s+of\s+philosophy", r"doctoral"]),
    ("mba", [r"M\.?\s?B\.?\s?A\.?", r"PGDM", r"PGP"], [r"master'?s?\s+of\s+business\s+administration", r"executive\s+mba",
                                                         r"post\s*graduate\s+diploma\s+in\s+management"]),
    ("master", [r"M\.?\s?Tech\.?", r"M\.?\s?Sc\.?", r"M\.?\s?Eng\.?", r"MCA", r"M\.?\s?Com\.?", r"M\.?\s?Phil\.?",
                r"LL\.?\s?M\.?", r"MFA", r"MPH", r"MPA", r"M\.\s?S\.", r"MS", r"M\.\s?A\.", r"MA", r"M\.\s?E\.", r"ME"],
     [r"master'?s?" + _OF, r"post\s*-?\s*graduate(?!\s+diploma)", r"graduate\s+degree"]),
    ("bachelor", [r"B\.?\s?Tech\.?", r"B\.?\s?Sc\.?", r"B\.?\s?Eng\.?", r"BCA", r"B\.?\s?Com\.?", r"BBA", r"BBM", r"BFA",
                  r"LL\.?\s?B\.?", r"B\.?\s?Arch\.?", r"B\.?\s?Ed\.?", r"B\.?\s?Pharm\.?", r"BDS", r"MBBS", r"B\.\s?E\.", r"BE",
                  r"B\.\s?S\.", r"BS", r"B\.\s?A\.", r"BA"],
     [r"bachelor'?s?" + _OF, r"undergraduate", r"baccalaureate"]),
    ("associate", [r"A\.\s?A\.\s?S\.?", r"AAS", r"A\.\s?S\.", r"A\.\s?A\.", r"AS", r"AA"],
     [r"associate'?s?\s+degree", r"associate\s+of\s+[A-Za-z]+(?:\s+[A-Za-z]+){0,2}", r"associate\s+in\s+[A-Za-z]+"]),
    ("diploma", [r"PGD"], [r"post\s*graduate\s+diploma", r"advanced\s+diploma", r"diploma", r"polytechnic"]),
    ("high_school", [r"HSC", r"SSC", r"XII", r"ISC", r"GED", r"A[- ]levels?"],
     [r"high\s+school", r"higher\s+secondary", r"senior\s+secondary", r"secondary\s+school", r"12th", r"10th", r"class\s+(?:10|12|x|xii)",
      r"gcse", r"a[- ]levels?", r"cbse", r"icse", r"matriculation", r"intermediate", r"grade\s+(?:10|12)", r"ib\s+diploma"]),
    ("certificate", [], [r"nanodegree", r"certificate\s+(?:in|of|program)", r"professional\s+certificate"]),
]
_LEVEL_PRIORITY = {name: i for i, (name, _, _) in enumerate(_DEGREE_SPECS)}

_COMPILED: List[Tuple[str, "re.Pattern[str]", bool]] = []
for _lvl, _abbr, _words in _DEGREE_SPECS:
    if _abbr:
        _COMPILED.append((_lvl, re.compile(r"(?<![A-Za-z0-9.])(?:" + "|".join(_abbr) + r")(?![A-Za-z0-9])"), True))
    if _words:
        _COMPILED.append((_lvl, re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(_words) + r")(?![A-Za-z0-9])", re.I), False))

_STRONG_ABBR = re.compile(r"^(?:B\.?\s?Tech|M\.?\s?Tech|Ph\.?\s?D|MBA|BCA|MCA|B\.?\s?Sc|M\.?\s?Sc|BBA|B\.?\s?Com|M\.?\s?Com|B\.?\s?Eng|M\.?\s?Eng)", re.I)
_FIELD_ABBR = {
    "CSE": "Computer Science and Engineering", "CS": "Computer Science", "IT": "Information Technology",
    "ECE": "Electronics and Communication Engineering", "EEE": "Electrical and Electronics Engineering",
    "EE": "Electrical Engineering", "ME": "Mechanical Engineering", "CE": "Civil Engineering",
    "ISE": "Information Science and Engineering", "AI&ML": "Artificial Intelligence and Machine Learning",
    "AIML": "Artificial Intelligence and Machine Learning", "AI": "Artificial Intelligence",
    "DS": "Data Science", "SE": "Software Engineering", "CSIT": "Computer Science and Information Technology",
}
_INST_CUES = re.compile(
    r"\b(?:university|college|institute|institution|school|academy|polytechnic|universit[aäéy]t?|universidad|universit[eé]|vidyalaya|"
    r"vidyapeeth|iit|nit|iiit|bits|iim|iisc|institut|hochschule|politecnico|lyceum|gymnasium|mit|stanford|harvard|oxford|"
    r"cambridge|caltech|princeton|yale|cornell|columbia|carnegie\s+mellon|cmu|georgia\s+tech|ucla|berkeley|campus|sschool)\b",
    re.I,
)
_STOP_FIELD = re.compile(r"\b(?:university|college|institute|school|academy|polytechnic|gpa|cgpa|percentage|grade|score|marks|expected|graduat\w*|from|at)\b", re.I)
_GPA_RE = [
    re.compile(r"(?:GPA|CGPA|CPI|SGPA|Grade|Aggregate|Score|Marks)\s*[:\-–]?\s*(\d+(?:\.\d+)?)\s*(?:/|out\s+of)\s*(\d+(?:\.\d+)?)", re.I),
    re.compile(r"(\d(?:\.\d{1,2})?)\s*/\s*(4(?:\.0+)?|5(?:\.0+)?|10(?:\.0+)?)\s*(?:GPA|CGPA)?", re.I),
    re.compile(r"(?:GPA|CGPA|CPI|SGPA|Grade)\s*[:\-–]?\s*(\d+(?:\.\d+)?)", re.I),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:CGPA|GPA|CPI)\b", re.I),
]
_PCT_RE = re.compile(r"(?:Percentage|Aggregate|Marks|Score)?\s*[:\-–]?\s*(\d{2}(?:\.\d+)?)\s*%", re.I)


def find_degree(text: str, strong_only: bool = False) -> Optional[Tuple[str, str, int, int]]:
    """Return ``(level, raw, start, end)`` of the leftmost degree cue."""
    best: Optional[Tuple[int, int, str, str, int]] = None
    for lvl, rx, _cs in _COMPILED:
        for m in rx.finditer(text):
            raw = m.group(0)
            letters = re.sub(r"[^A-Za-z]", "", raw)
            if _cs and len(letters) <= 2 and lvl != "doctorate":
                # bare two-letter abbreviations (BE, ME, MS, BA ...) only at the start of a phrase
                before = text[: m.start()].rstrip()
                if before and not before.endswith((",", "|", "-", "–", "—", "(", ":", "•")):
                    continue
            if strong_only and _cs and not _STRONG_ABBR.match(raw) and lvl not in ("doctorate",):
                continue
            key = (m.start(), _LEVEL_PRIORITY[lvl])
            if best is None or key < (best[0], best[1]):
                best = (m.start(), _LEVEL_PRIORITY[lvl], lvl, raw, m.end())
            break
    if best is None:
        return None
    return best[2], best[3].strip(), best[0], best[4]


def extract_gpa(text: str) -> Tuple[Optional[float], Optional[float]]:
    for rx in _GPA_RE[:2]:
        m = rx.search(text)
        if m:
            val, scale = float(m.group(1)), float(m.group(2))
            if scale in (4.0, 5.0, 10.0, 100.0) and 0 < val <= scale:
                return val, scale
            if scale in (4.0, 5.0, 10.0, 100.0):
                return None, None          # an explicit scale contradicted by the value: do not guess
    m = _GPA_RE[2].search(text) or _GPA_RE[3].search(text)
    if m:
        val = float(m.group(1))
        label = m.group(0).lower()
        if val <= 4.0:
            return val, 4.0
        if val <= 10.0:
            return val, 10.0 if ("cgpa" in label or "cpi" in label or val > 5.0) else 5.0
        if val <= 100:
            return val, 100.0
    m = _PCT_RE.search(text)
    if m:
        val = float(m.group(1))
        if 30 <= val <= 100:
            return val, 100.0
    return None, None


def _clean_field(raw: str) -> str:
    f = raw.strip(" ,.;:-–—()")
    f = re.sub(r"\s+", " ", f)
    if not f or len(f) < 2 or len(f) > 70 or len(f.split()) > 9:
        return ""
    if _STOP_FIELD.search(f) or _INST_CUES.search(f) or re.search(r"\d{4}", f):
        return ""
    up = f.upper().replace(" ", "")
    if up in {"HSC", "SSC", "CBSE", "ICSE", "ISC", "XII", "GED"}:
        return ""
    if up in _FIELD_ABBR:
        return _FIELD_ABBR[up]
    if f.islower():
        f = f.title()
    return f


def extract_field(text: str, end: int, raw_degree: str) -> str:
    rest = text[end:]
    # "of X" where X is a subject, e.g. "Bachelor of Computer Science" (X already inside the raw degree)
    m = re.match(r"(?i)^(?:bachelor|master)'?s?\s+of\s+(.+)$", raw_degree.strip())
    if m:
        sub = m.group(1).strip()
        if sub.lower() not in {"science", "arts", "technology", "engineering", "commerce", "laws", "law", "architecture",
                               "education", "pharmacy", "business administration", "business", "management", "fine arts",
                               "medicine", "surgery", "computer applications", "computer application", "philosophy",
                               "public health", "applied science", "nursing", "music", "social work"}:
            return _clean_field(sub)
    rest = re.sub(r"^[\s.,:;\-–—]*", "", rest)
    rest = re.sub(r"(?i)^(?:in|of|with|major(?:ing)?\s+in|specializ(?:ation|ing)\s+in|specialis(?:ation|ing)\s+in|concentration\s+in|major:?|stream:?|branch:?)\s+", "", rest)
    if rest.startswith("("):
        mm = re.match(r"\(([^)]{2,70})\)", rest)
        if mm:
            return _clean_field(mm.group(1))
    # cut at first delimiter
    cut = re.split(r"\s*(?:,|\||\t|;|\s[-–—]\s|\(|\bfrom\b|\bat\b|\b(?:19|20)\d{2}\b|\bGPA\b|\bCGPA\b|\bgrade\b|\bscore\b|\bexpected\b)", rest, maxsplit=1, flags=re.I)[0]
    return _clean_field(cut)


def extract_institution(text: str) -> str:
    rngs = find_date_ranges(text)
    if rngs:
        text = strip_ranges(text, rngs)
    frags = [f.strip() for f in re.split(r"\s*(?:\||\t|•|·|\s[-–—]\s)\s*", text) if f.strip()]
    for frag in frags:
        parts = [p.strip() for p in frag.split(",") if p.strip()]
        for pi, p in enumerate(parts):
            if _INST_CUES.search(p):
                inst = p
                if re.match(r"(?i)^(?:university|institute|college)\s+of\b", inst) and pi + 1 < len(parts):
                    nxt = parts[pi + 1]
                    last_word = inst.split()[-1].lower()
                    if (re.fullmatch(r"[A-Z][A-Za-z.'\- ]{2,25}", nxt) and len(nxt.split()) <= 2 and nxt.upper() not in ("USA", "UK")
                            and nxt not in {"USA", "UK"} and nxt.lower() != last_word and not re.fullmatch(r"[A-Z]{2}", nxt)
                            and not re.search(r"(?i)\b(?:university|college|institute|school)\b", nxt) and nxt.lower() not in {"india", "canada", "australia"}):
                        inst = f"{inst}, {nxt}"
                inst = re.sub(r"\s+\b(?:19|20)\d{2}\b.*$", "", inst)
                inst = re.sub(r"(?i)\b(?:gpa|cgpa|percentage|grade)\b.*$", "", inst).strip(" ,.-–—")
                if 3 <= len(inst) <= 90:
                    return inst
    return ""


# --------------------------------------------------------------------------- main

def parse_education(region: Sequence[Tuple[int, Line]], fallback: bool = False) -> List[EducationEntry]:
    n = len(region)
    anchors: List[Tuple[int, Tuple[str, str, int, int]]] = []
    for i, (_, ln) in enumerate(region):
        t = ln.text
        d = find_degree(t, strong_only=fallback)
        if d:
            # lines that are clearly not education (long sentences)
            if len(t.split()) > 30:
                continue
            if fallback and not (_INST_CUES.search(t) or re.search(r"\b(?:19|20)\d{2}\b", t) or d[0] in ("doctorate", "mba")):
                continue
            anchors.append((i, d))
    if not anchors:
        return []
    anchor_idx = [a[0] for a in anchors]
    extra: Dict[int, List[int]] = {a: [] for a in anchor_idx}
    # attribute bookkeeping per anchor: which of institution / date / gpa it already has
    have: Dict[int, set] = {}
    for ai, (_lvl, raw, _s, _e) in anchors:
        t = region[ai][1].text
        attrs = set()
        if extract_institution(re.sub(re.escape(raw), " ", t, count=1)):
            attrs.add("inst")
        if find_date_ranges(t):
            attrs.add("date")
        if extract_gpa(t)[0] is not None:
            attrs.add("gpa")
        have[ai] = attrs
    first_anchor = anchor_idx[0]
    inst_first = any(
        _INST_CUES.search(region[i][1].text) and i not in extra for i in range(0, first_anchor)
    )

    def line_attrs(t: str) -> List[str]:
        out = []
        if _INST_CUES.search(t):
            out.append("inst")
        if find_date_ranges(t):
            out.append("date")
        if re.search(r"(?i)\bgpa\b|\bcgpa\b|\bpercentage\b|\d%|/\s*(?:4|10)\b", t):
            out.append("gpa")
        return out

    for i in range(n):
        if i in extra:
            continue
        ln = region[i][1]
        t = ln.text
        attrs = line_attrs(t)
        if not attrs or len(t.split()) > 18:
            continue
        if ln.is_bullet and "inst" not in attrs and "gpa" not in attrs:
            continue
        prev = max((a for a in anchor_idx if a < i), default=None)
        nxt = min((a for a in anchor_idx if a > i), default=None)
        target = None
        primary = attrs[0]
        if prev is not None and nxt is not None:
            prev_needs = primary not in have[prev]
            next_needs = primary not in have[nxt]
            if not inst_first:
                target = prev if prev_needs else (nxt if next_needs else prev)
            else:
                adjacent = i - prev <= 1
                if primary == "inst":
                    target = nxt if next_needs else (prev if prev_needs else nxt)
                else:
                    target = prev if (prev_needs and adjacent) else (nxt if next_needs else prev)
        elif prev is not None:
            target = prev
        elif nxt is not None:
            target = nxt
        if target is None or abs(target - i) > 4:
            continue
        extra[target].append(i)
        have[target].update(attrs)
    entries: List[EducationEntry] = []
    for ai, (lvl, raw, s, e) in anchors:
        line = region[ai][1].text
        own = dict.fromkeys([ai] + extra[ai])
        idxs = sorted(own)
        texts = [region[j][1].text for j in idxs]
        blob = " | ".join(texts)
        field = extract_field(line, e, raw)
        # institution: own line first, then neighbours
        inst = extract_institution(re.sub(re.escape(raw), " ", line, count=1))
        if not inst:
            for j in idxs:
                if j != ai:
                    inst = extract_institution(region[j][1].text)
                    if inst:
                        break
        # dates
        sy = ey = None
        for j in idxs:
            rs = find_date_ranges(region[j][1].text)
            if rs:
                r = rs[0]
                if r.kind in ("range", "season"):
                    sy = r.start // 12 if r.start is not None else None
                    ey = None if r.is_current else (r.end // 12 if r.end is not None else None)
                else:
                    ey = (r.start // 12) if r.start is not None else None
                break
        gpa, scale = extract_gpa(blob)
        if lvl == "high_school":
            field = field if field else ""
        conf = 0.3 + (0.25 if inst else 0) + (0.2 if field else 0) + (0.15 if (sy or ey) else 0) + (0.1 if gpa else 0)
        if fallback:
            conf *= 0.8
        entries.append(EducationEntry(
            degree_level=lvl, degree_raw=raw, field=field, institution=inst, start_year=sy, end_year=ey,
            gpa=gpa, gpa_scale=scale, confidence=min(conf, 1.0),
        ))
    # dedupe
    seen = set()
    out = []
    for en in entries:
        key = (en.degree_level, en.field.lower(), en.institution.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(en)
    return out
