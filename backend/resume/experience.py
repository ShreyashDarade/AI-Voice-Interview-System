"""Work-experience extraction.

Every date range anchors one entry.  Title / company / location are derived
from the anchor line (minus the date text) and, when something is missing,
from the neighbouring header-like lines (above first, then below), using
job-title and company-suffix lexicons plus separator semantics
(``Title at Company``, ``Title | Company``, ``Company - Title``,
``Title, Company``).  Lines until the next entry are the entry's bullets.
Promotions at the same company (group header + several dated roles) are
handled by dropping the group header and inheriting its company.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .dates import DateRange, find_date_ranges, parse_duration, strip_ranges
from .extract import Line
from .lexicons import (
    ACTION_VERBS, CITIES, COUNTRIES, INDIAN_STATES, LOCATION_WORDS, US_STATES, CA_PROVINCES,
    company_score, has_title_word,
)
from .models import ExperienceEntry
from .util import current_ym, ym_str

_LABEL_PREFIXES = (
    "responsibilities", "responsibility", "achievements", "key achievements", "environment", "technologies",
    "tech stack", "tools", "description", "roles", "role", "duties", "tech", "skills", "projects", "project",
    "client", "domain", "team size", "highlights", "key responsibilities", "contribution", "contributions",
    "technology", "stack", "accomplishments", "impact",
)
_LABELLED_RE = re.compile(
    r"^\s*(role|designation|position|job\s+title|title|company|employer|organi[sz]ation|client|location|place|duration|period|tenure|dates?)\s*[:\-–]\s*(\S.*)$",
    re.I,
)
_LABEL_KIND = {"role": "title", "designation": "title", "position": "title", "job title": "title", "title": "title",
               "company": "company", "employer": "company", "organization": "company", "organisation": "company",
               "client": "company", "location": "location", "place": "location",
               "duration": "date", "period": "date", "tenure": "date", "date": "date", "dates": "date"}
_EMP_TYPE_RE = [
    ("internship", re.compile(r"\bintern(?:ship)?s?\b|\btrainee\b|\bapprentice(?:ship)?\b", re.I)),
    ("freelance", re.compile(r"\bfreelanc\w*|\bself[- ]employed\b|\bindependent consultant\b", re.I)),
    ("contract", re.compile(r"\bcontract(?:or|ual)?\b|\btemporary\b|\bc2h\b|\bcontract[- ]to[- ]hire\b", re.I)),
    ("part_time", re.compile(r"\bpart[- ]time\b", re.I)),
]
_NAME_SETS = {w.lower() for w in list(US_STATES.values()) + list(CA_PROVINCES.values()) + INDIAN_STATES + COUNTRIES + CITIES}
_US_CODES = set(US_STATES) | set(CA_PROVINCES)
_LOC_TAIL = re.compile(
    r"(?:,|\s[-–—|]\s|\s{2,}|\t)\s*((?:[A-Z][A-Za-z.'\-]+\s){0,2}[A-Z][A-Za-z.'\-]+,\s*[A-Z]{2}(?:,\s*USA?)?|[A-Z][A-Za-z.'\- ]+,\s*[A-Z][A-Za-z.'\- ]+(?:,\s*[A-Z][A-Za-z.'\- ]+)?|Remote|Hybrid|On-?site)\s*$"
)
_SPLIT_RE = re.compile(r"\s*(?:\t|\||·|•|\s@\s|@|\s[-–—]\s|\s—\s|\sat\s)\s*", re.I)


# --------------------------------------------------------------------------- helpers

def _words(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9.&'’+#\-/]*", text)


def is_location_text(text: str) -> bool:
    t = text.strip(" ,.()")
    if not t:
        return False
    low = t.lower()
    if low in LOCATION_WORDS or low in _NAME_SETS:
        return True
    m = re.fullmatch(r"([A-Z][A-Za-z.'\- ]+),\s*([A-Z]{2})", t)
    if m and m.group(2) in _US_CODES:
        return True
    parts = [p.strip().lower() for p in t.split(",")]
    if len(parts) >= 2 and all(p in _NAME_SETS for p in parts):
        return True
    return False


def _split_location(text: str) -> Tuple[str, str]:
    """Strip a trailing location from ``text``; returns ``(rest, location)``."""
    m = _LOC_TAIL.search(text)
    if m:
        loc = m.group(1).strip()
        if is_location_text(loc) or re.fullmatch(r"Remote|Hybrid|On-?site", loc, re.I):
            return text[: m.start()].strip(" ,|-–—"), loc
    # trailing single known city/state/country
    toks = text.split()
    for n in (3, 2, 1):
        if len(toks) > n:
            tail = " ".join(toks[-n:]).strip(" ,")
            if tail.lower() in _NAME_SETS and text[: text.rfind(tail)].rstrip().endswith((",", "-", "–", "|")):
                return text[: text.rfind(tail)].strip(" ,|-–—"), tail
    return text, ""


def title_score(text: str) -> float:
    toks = [t.lower().strip(".,") for t in re.findall(r"[A-Za-z][A-Za-z.\-]*", text)]
    if not toks or not has_title_word(text):
        return 0.0
    from .lexicons import TITLE_WORDS

    n = sum(1 for t in toks if t in TITLE_WORDS)
    return 1.0 + 0.25 * min(n - 1, 3) if n else 0.5


def _cap_fraction(text: str) -> float:
    small = {"of", "and", "the", "at", "for", "in", "on", "de", "la", "&", "to", "a", "an", "&amp;", "-", "–", "—", "|"}
    toks = [t for t in text.split() if t.lower() not in small and any(c.isalpha() for c in t)]
    if not toks:
        return 0.0
    return sum(1 for t in toks if t[0].isupper() or t.isupper() or any(c.isdigit() for c in t[:1])) / len(toks)


def labelled(text: str) -> Optional[Tuple[str, str]]:
    """``Role: Systems Engineer`` -> ``("title", "Systems Engineer")``."""
    m = _LABELLED_RE.match(text)
    if not m:
        return None
    kind = _LABEL_KIND.get(re.sub(r"\s+", " ", m.group(1).lower()))
    return (kind, m.group(2).strip()) if kind else None


def is_header_like(line: Line, text: Optional[str] = None) -> bool:
    t = (text if text is not None else line.text).strip()
    if line.is_bullet and not line.numbered:
        return False
    lab = labelled(t)
    if lab and lab[0] != "date":
        return bool(lab[1]) and len(lab[1]) <= 90
    if not t or line.rule_below and len(t) > 80:
        return False
    if len(t) > 120:
        return False
    ws = t.split()
    if len(ws) > 14 or len(t) < 2:
        return False
    low = t.lower().rstrip(":")
    if any(low == p or low.startswith(p + ":") or low.startswith(p + " -") for p in _LABEL_PREFIXES):
        return False
    first = ws[0].lower().strip(",.")
    if first in ACTION_VERBS:
        return False
    if re.search(r"[.!?]$", t) and not re.search(r"\b(?:inc|ltd|pvt|llc|corp|co|sr|jr|u\.?s\.?a?|ltd\.?)\.$", t, re.I):
        if len(ws) > 4:
            return False
    if re.fullmatch(r"[\W_]+", t):
        return False
    if "@" in t or re.search(r"https?://", t):
        return False
    if _cap_fraction(t) < 0.5 and not has_title_word(t):
        return False
    return True


def employment_type(*texts: str) -> str:
    blob = " ".join(texts)
    for name, rx in _EMP_TYPE_RE:
        if rx.search(blob):
            return name
    return "full_time"


# --------------------------------------------------------------------------- header classification

@dataclass
class _Header:
    title: str = ""
    company: str = ""
    location: str = ""
    certain: float = 0.0       # 0..1 how sure we are about the role assignment


def _strip_parentheticals(piece: str) -> Tuple[str, List[str]]:
    notes: List[str] = []

    def repl(m: "re.Match[str]") -> str:
        notes.append(m.group(1).strip())
        return " "

    return re.sub(r"\(([^()]{1,40})\)", repl, piece), notes


def classify_header(pieces: Sequence[str]) -> _Header:
    frags: List[Tuple[str, str]] = []     # (text, separator_before)
    location = ""
    forced = _Header()
    plain: List[str] = []
    for piece in pieces:
        lab = labelled(piece)
        if lab:
            kind, val = lab
            if kind == "title" and not forced.title:
                forced.title = val.strip(" ,|-–—")
            elif kind == "company" and not forced.company:
                forced.company = val.strip(" ,|-–—")
            elif kind == "location" and not forced.location:
                forced.location = val.strip(" ,|-–—")
            continue
        plain.append(piece)
    pieces = plain
    for piece in pieces:
        piece, notes = _strip_parentheticals(piece)
        for n in notes:
            if is_location_text(n) or n.lower() in ("remote", "hybrid", "on-site", "onsite"):
                location = location or n
        piece, loc = _split_location(piece.strip(" ,|-–—"))
        if loc and not location:
            location = loc
        # split on strong separators, remembering which one
        pos = 0
        last_sep = ""
        for m in _SPLIT_RE.finditer(piece):
            seg = piece[pos:m.start()]
            if seg.strip():
                frags.append((seg.strip(" ,"), last_sep))
            last_sep = m.group(0).strip().lower() or "|"
            pos = m.end()
        tail = piece[pos:]
        if tail.strip(" ,"):
            frags.append((tail.strip(" ,"), last_sep))
    # comma-split fragments that contain a title part and a non-title part
    expanded: List[Tuple[str, str]] = []
    for text, sep in frags:
        if "," in text:
            parts = [p.strip() for p in text.split(",") if p.strip()]
            merged: List[str] = []
            for p in parts:
                if merged and p.lower().rstrip(".") in ("inc", "llc", "ltd", "pvt ltd", "pvt", "corp", "co", "llp", "limited", "pvt. ltd"):
                    merged[-1] += ", " + p
                else:
                    merged.append(p)
            if len(merged) > 1:
                scores = [title_score(p) > 0 for p in merged]
                if any(scores) and not all(scores):
                    for k, p in enumerate(merged):
                        expanded.append((p, sep if k == 0 else ","))
                    continue
                locs = [is_location_text(p) for p in merged]
                if any(locs) and not all(locs):
                    keep = [p for p, l in zip(merged, locs) if not l]
                    if not location:
                        location = ", ".join(p for p, l in zip(merged, locs) if l)
                    expanded.append((", ".join(keep), sep))
                    continue
        expanded.append((text, sep))
    header = _Header(location=location)
    unknown: List[Tuple[str, str]] = []
    titles: List[Tuple[float, str]] = []
    companies: List[Tuple[float, str]] = []
    for text, sep in expanded:
        t = text.strip()
        if not t or not re.search(r"[A-Za-z]", t):
            continue
        if is_location_text(t) and not title_score(t):
            header.location = header.location or t
            continue
        ts, cs = title_score(t), company_score(t)
        if cs >= 2.0 or (cs >= 1.0 and ts == 0):
            companies.append((cs, t))
        elif ts > 0:
            titles.append((ts, t))
        elif cs > 0:
            companies.append((cs, t))
        else:
            unknown.append((t, sep))
    certain = 0.0
    if titles:
        header.title = titles[0][1]
        certain += 0.5
    if companies:
        header.company = max(companies, key=lambda c: c[0])[1]
        certain += 0.5
    # leftover fragments
    for t, sep in unknown:
        if header.title and header.company:
            break
        if not header.company and (header.title or sep in ("at", "@")):
            header.company = t
            certain += 0.25
        elif not header.title and header.company:
            header.title = t
            certain += 0.25
        elif not header.title and not header.company:
            # first unknown -> title, later unknown -> company ("Title, Company")
            if unknown and t == unknown[0][0] and len(unknown) > 1:
                header.title = t
            else:
                header.company = t
            certain += 0.15
    if len(titles) > 1 and not header.company:
        # "Software Engineer | Data Platform Engineer": second title-ish fragment is likely a team
        pass
    # "Company – Title" with both title-ish: prefer lexicon, keep as is
    header.certain = min(certain, 1.0)
    # explicitly labelled values ("Role: ...", "Company: ...") win
    if forced.title:
        if header.title and not header.company and not forced.company and company_score(header.title) >= 0.3 and not has_title_word(header.title):
            header.company = header.title
        header.title = forced.title
        header.certain = min(1.0, header.certain + 0.5)
    if forced.company:
        header.company = forced.company
        header.certain = min(1.0, header.certain + 0.5)
    if forced.location:
        header.location = forced.location
    # tidy
    header.title = header.title.strip(" ,|-–—")
    header.company = header.company.strip(" ,|-–—")
    return header


# --------------------------------------------------------------------------- core

@dataclass
class _Anchor:
    idx: int                    # index within region
    rng: DateRange
    remainder: str
    above: List[int] = field(default_factory=list)
    below: List[int] = field(default_factory=list)
    extra: List[int] = field(default_factory=list)     # repeated date lines ("Duration: ...") folded into this entry


def parse_experience(
    region: Sequence[Tuple[int, Line]],
    section_heading: str = "",
    fallback: bool = False,
) -> Tuple[List[ExperienceEntry], List[str]]:
    """Parse one contiguous region of ``(global_line_idx, Line)`` pairs."""
    warnings: List[str] = []
    n = len(region)
    texts = [ln.text for _, ln in region]
    # 1. anchors
    anchors: List[_Anchor] = []
    for i, (_, ln) in enumerate(region):
        if ln.is_bullet and not ln.numbered:
            continue
        rngs = find_date_ranges(ln.text)
        if not rngs:
            continue
        # prefer a real range over a lone point
        rng = next((r for r in rngs if r.kind in ("range", "season")), rngs[0])
        rem = strip_ranges(ln.text, [rng])
        rem = re.sub(r"(?i)^\s*(?:duration|period|tenure|dates?)\s*[:\-–]?\s*$", "", rem).strip()
        words = _words(rem)
        starts_or_ends = rng.span[0] <= 2 or rng.span[1] >= len(ln.text) - 2 or ln.text[rng.span[1]:].strip(" )").startswith(("|", "-"))
        if len(words) > 14 and not starts_or_ends:
            continue
        if rem and not is_header_like(ln, rem) and len(words) > 7:
            continue
        if rng.kind == "point" and (not rem or not (has_title_word(rem) or company_score(rem) >= 1.0)):
            continue
        # a point date inside a long sentence is not an anchor
        if rng.kind == "point" and len(words) > 9:
            continue
        anchors.append(_Anchor(i, rng, rem))
    if not anchors:
        # undated entries with explicit durations ("Software Engineer - 2 years")
        entries: List[ExperienceEntry] = []
        for i, (_, ln) in enumerate(region):
            if ln.is_bullet:
                continue
            months = parse_duration(ln.text)
            if months and is_header_like(ln) and has_title_word(ln.text):
                h = classify_header([re.sub(r"\(?\b\d+(?:\.\d+)?\s*\+?\s*(?:years?|yrs?|months?|mos?)\b\)?", " ", ln.text)])
                entries.append(ExperienceEntry(
                    title=h.title, company=h.company, location=h.location, duration_months=months,
                    confidence=0.3, employment_type=employment_type(ln.text), date_text=ln.text[:60],
                    date_precision="duration", line_range=(region[i][0], region[i][0] + 1),
                ))
        if entries:
            warnings.append("experience_durations_only")
        elif n >= 3 and not fallback:
            warnings.append("experience_no_dates_found")
        return entries, warnings

    # a date-only line repeating the previous anchor's dates ("Duration: Apr 2019 - Present") is not a new entry
    folded: List[_Anchor] = []
    for a in anchors:
        prev = folded[-1] if folded else None
        if (prev is not None and not a.remainder and a.idx - prev.idx <= 3
                and (a.rng.start, a.rng.end, a.rng.is_current) == (prev.rng.start, prev.rng.end, prev.rng.is_current)):
            prev.extra.append(a.idx)
            continue
        folded.append(a)
    anchors = folded
    anchor_idx = {a.idx for a in anchors}
    claimed: Set[int] = set(anchor_idx)
    for a in anchors:
        claimed.update(a.extra)

    def header_like_free(j: int) -> bool:
        return 0 <= j < n and j not in claimed and is_header_like(region[j][1])

    # 2. neighbours (document order, sequential greedy)
    for k, a in enumerate(anchors):
        h0 = classify_header([a.remainder]) if a.remainder else _Header()
        need_t = not h0.title
        need_c = not h0.company
        # above
        above: List[int] = []
        j = a.idx - 1
        limit = 2 if (not a.remainder) else 2
        while len(above) < limit and header_like_free(j):
            cand = region[j][1].text
            hh = classify_header([cand])
            gives = (need_t and hh.title) or (need_c and hh.company) or (hh.location and not h0.location)
            if not gives and not (not a.remainder and not above):
                break
            above.insert(0, j)
            if hh.title:
                need_t = False
            if hh.company:
                need_c = False
            j -= 1
            if not need_t and not need_c:
                break
        for j2 in above:
            claimed.add(j2)
        a.above = above
        # below (only when something still missing or a bare location/emp-type line)
        below: List[int] = []
        j = a.idx + 1
        while len(below) < 2 and header_like_free(j) and j not in anchor_idx:
            cand = region[j][1].text
            hh = classify_header([cand])
            gives = (need_t and hh.title) or (need_c and hh.company) or (hh.location and not (h0.location or any(classify_header([region[x][1].text]).location for x in above)))
            # a lone short line right under the anchor with nothing else known
            if not gives and not (need_t and need_c and not below):
                break
            below.append(j)
            claimed.add(j)
            if hh.title:
                need_t = False
            if hh.company:
                need_c = False
            j += 1
            if not need_t and not need_c and not hh.location:
                break
        a.below = below

    # 3. build entries
    today = current_ym()
    entries = []
    spans: List[Tuple[int, int]] = []
    for k, a in enumerate(anchors):
        first = min([a.idx] + a.above)
        nxt_first = min([anchors[k + 1].idx] + anchors[k + 1].above) if k + 1 < len(anchors) else n
        spans.append((first, nxt_first))
    for k, a in enumerate(anchors):
        first, end_excl = spans[k]
        header_lines = sorted(a.above + [a.idx] + a.below + a.extra)
        pieces = []
        for j in header_lines:
            if j in a.extra:
                continue
            pieces.append(a.remainder if j == a.idx else region[j][1].text)
        pieces = [p for p in pieces if p]
        h = classify_header(pieces)
        body_idx = [j for j in range(first, end_excl) if j not in set(header_lines) and j != a.idx]
        bullets = [region[j][1].text.strip() for j in body_idx if region[j][1].text.strip()]
        rng = a.rng
        e = ExperienceEntry(
            title=h.title, company=h.company, location=h.location,
            bullets=bullets, date_text=rng.raw.strip(),
            date_precision=rng.start_prec if rng.kind != "range" else ("year" if "year" in (rng.start_prec, rng.end_prec) else rng.start_prec),
            employment_type=employment_type(h.title, " ".join(pieces)),
        )
        if e.employment_type == "full_time" and "intern" in section_heading.lower():
            e.employment_type = "internship"
        e.line_range = (region[first][0], region[end_excl - 1][0] + 1 if end_excl - 1 >= first else region[first][0] + 1)
        # dates
        e.start_ym = rng.start
        if rng.kind == "point":
            e.end_ym = None
        elif rng.is_current:
            e.is_current = True
            e.end_ym = today
        else:
            e.end_ym = rng.end
        if e.start_ym is not None:
            e.start = ym_str(e.start_ym)
        if rng.is_current:
            e.end = None
        elif e.end_ym is not None:
            e.end = ym_str(e.end_ym)
        issues: List[str] = []
        if e.start_ym is not None and e.end_ym is not None:
            if e.start_ym > e.end_ym and not rng.is_current:
                issues.append("start_after_end")
            elif e.start_ym > today + 1:
                issues.append("future_start")
                e.end_ym = None
            else:
                if e.end_ym > today and not rng.is_current:
                    issues.append("future_end")
                    e.end_ym = today
                    e.end = ym_str(today)
                e.duration_months = e.end_ym - e.start_ym + 1
        e.issues = issues
        conf = 0.2
        conf += 0.3 if rng.kind == "range" and rng.confidence >= 0.9 else (0.2 if rng.kind in ("range", "season") else 0.05)
        conf += 0.2 if e.title else 0.0
        conf += 0.15 if e.company else 0.0
        conf += 0.1 if e.bullets else 0.0
        conf += 0.05 * h.certain
        if issues:
            conf -= 0.25
        e.confidence = max(0.05, min(conf, 1.0))
        entries.append(e)

    entries = _postprocess(entries)
    return entries, warnings


def _postprocess(entries: List[ExperienceEntry]) -> List[ExperienceEntry]:
    # group headers: "Acme Corp (2018 - Present)" followed by dated roles inside that range
    drop: Set[int] = set()
    for i, e in enumerate(entries[:-1]):
        nxt = entries[i + 1]
        if (not e.title and e.company and not e.bullets and nxt.title and
                e.start_ym is not None and nxt.start_ym is not None and nxt.start_ym >= e.start_ym - 1 and
                (nxt.end_ym or 10 ** 9) <= (e.end_ym or 10 ** 9) + 1):
            if not nxt.company or nxt.company.lower() == e.company.lower():
                nxt.company = e.company
                nxt.confidence = min(1.0, nxt.confidence + 0.1)
                drop.add(i)
    entries = [e for i, e in enumerate(entries) if i not in drop]
    # inherit company for consecutive title-only roles
    for i in range(1, len(entries)):
        e, prev = entries[i], entries[i - 1]
        if e.title and not e.company and prev.company:
            e.company = prev.company
            e.confidence = max(0.05, e.confidence - 0.0)
    # dedupe identical
    seen = set()
    out = []
    for e in entries:
        key = (e.title.lower(), e.company.lower(), e.start, e.end)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def valid_interval(e: ExperienceEntry) -> Optional[Tuple[int, int]]:
    if e.start_ym is None or e.end_ym is None or e.start_ym > e.end_ym:
        return None
    return (e.start_ym, e.end_ym)


def seniority_from(months: int, titles: Sequence[str]) -> str:
    levels = ["fresher", "junior", "mid", "senior", "lead"]
    bounds = [0, 12, 36, 60, 96]
    lvl = 0
    for i, b in enumerate(bounds):
        if months >= b:
            lvl = i
    # title bump (never contradicts months: needs >= 60% of the next level's lower bound)
    bump_re = re.compile(r"\b(?:lead|principal|staff|head|director|manager|architect|vp|chief|cto)\b", re.I)
    recent = [t for t in titles[:2] if t]
    if any(bump_re.search(t) and not re.search(r"\bintern", t, re.I) for t in recent) and lvl < 4:
        if months >= 0.6 * bounds[lvl + 1]:
            lvl += 1
    return levels[lvl]
