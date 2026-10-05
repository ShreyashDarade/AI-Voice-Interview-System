"""Date-range parsing and interval arithmetic (month granularity).

Months are integers (``year * 12 + month - 1``, see :mod:`resume.util`).
Intervals are *inclusive* on both ends: ``Jan 2020 - Mar 2020`` is 3 months.

Conventions for imprecise dates (documented, deliberately conservative):

* year-only start  -> July of that year (January if the end is the same year);
* year-only end    -> June when the start is also year-only, else December;
  so ``2019 - 2021`` counts as exactly 24 months, not 36;
* ``Summer 2021`` -> Jun-Aug 2021 (low confidence); seasons map
  spring=Mar-May, summer=Jun-Aug, fall/autumn=Sep-Nov, winter=Dec-Feb;
* ``Present / Current / Till date / Ongoing / Now / To date`` -> today.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from .util import current_ym, ym

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_SEASON_RANGE = {"spring": (3, 5), "summer": (6, 8), "fall": (9, 11), "autumn": (9, 11), "winter": (12, 2)}
PRESENT_WORDS = (
    r"present|current(?:ly)?|till\s+date|till\s+now|till\s+today|till\s+present|until\s+now|until\s+date|"
    r"until\s+present|ongoing|now|today|date|to\s+date"
)

_TOKEN_RE = re.compile(
    rf"""
    (?P<mo_only>(?<![A-Za-z])(?P<mo_o>{_MON})\b\.?(?=\s*(?:-|–|—|to|until|through)\s*{_MON}\.?,?\s*['’]?\d{{2,4}}))
  | (?P<my>(?<![A-Za-z])(?P<m1>{_MON})\b\.?,?\s*(?:(?P<apos>['’`])\s?(?P<y2>\d{{2}})|(?P<y4>(?:19|20)\d{{2}}))(?!\d))
  | (?P<season>(?<![A-Za-z])(?P<sname>spring|summer|fall|autumn|winter)\s+(?P<sy>(?:19|20)\d{{2}})(?!\d))
  | (?P<mdy>(?<![A-Za-z])(?P<m3>{_MON})\b\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+(?P<y5>(?:19|20)\d{{2}})(?!\d))
  | (?P<ymn>(?<![\d/.\-])(?P<yy>(?:19|20)\d{{2}})\s*[/.\-]\s*(?P<ymm>0?[1-9]|1[0-2])(?![\d/]))
  | (?P<acad>(?<![\d/.\-])(?P<ay>(?:19|20)\d{{2}})\s*[-–/]\s*(?P<ay2>\d{{2}})(?!\d))
  | (?P<nmy>(?<![\d/.\-])(?P<nm>0?[1-9]|1[0-2])\s*[/.\-]\s*(?P<ny>(?:19|20)\d{{2}})(?![\d/]))
  | (?P<yr>(?<![\d$£€₹.,/])(?P<y>(?:19|20)\d{{2}})(?!\d|%|\+|\s*[kK]\b|,\d))
  | (?P<pres>(?<![A-Za-z])(?:{PRESENT_WORDS})(?![A-Za-z]))
    """,
    re.I | re.X,
)
_SEP_RE = re.compile(
    r"^[\s()\[\]]*(?:(?:[-–—−‒~]|to|until|till|through|thru|up\s+to|and)\s*)+[\s()\[\]]*$", re.I
)


@dataclass
class DateRange:
    start: Optional[int]            # month index or None
    end: Optional[int]              # month index or None (current)
    is_current: bool
    span: Tuple[int, int]           # char span in the source text
    raw: str
    kind: str                       # 'range' | 'point' | 'season'
    start_prec: str = "month"       # month | year | season
    end_prec: str = "month"
    confidence: float = 1.0

    @property
    def effective_end(self) -> Optional[int]:
        return current_ym() if self.is_current else self.end


@dataclass
class _Tok:
    kind: str                       # 'point' | 'pres' | 'season' | 'acad' | 'mo_only'
    start: int
    end: int
    year: Optional[int] = None
    month: Optional[int] = None
    prec: str = "month"
    season: Optional[str] = None
    raw: str = ""


def _full_year(y2: int) -> int:
    cur = current_ym() // 12 % 100
    return 2000 + y2 if y2 <= cur + 1 else 1900 + y2


def _tokens(text: str) -> List[_Tok]:
    toks: List[_Tok] = []
    for m in _TOKEN_RE.finditer(text):
        g = m.lastgroup
        raw = m.group(0)
        if m.group("mo_only"):
            toks.append(_Tok("mo_only", m.start(), m.end(), month=MONTHS[m.group("mo_o").lower().rstrip(".")], raw=raw))
        elif m.group("my"):
            y = int(m.group("y4")) if m.group("y4") else _full_year(int(m.group("y2")))
            toks.append(_Tok("point", m.start(), m.end(), y, MONTHS[m.group("m1").lower().rstrip(".")], "month", raw=raw))
        elif m.group("season"):
            toks.append(_Tok("season", m.start(), m.end(), int(m.group("sy")), None, "season", m.group("sname").lower(), raw))
        elif m.group("mdy"):
            toks.append(_Tok("point", m.start(), m.end(), int(m.group("y5")), MONTHS[m.group("m3").lower().rstrip(".")], "month", raw=raw))
        elif m.group("ymn"):
            yy, mm = int(m.group("yy")), int(m.group("ymm"))
            if mm == (yy + 1) % 100 and mm <= 12:
                toks.append(_Tok("acad", m.start(), m.end(), yy, None, "year", raw=raw))
            else:
                toks.append(_Tok("point", m.start(), m.end(), yy, mm, "month", raw=raw))
        elif m.group("acad"):
            ay, ay2 = int(m.group("ay")), int(m.group("ay2"))
            if ay2 == (ay + 1) % 100:
                toks.append(_Tok("acad", m.start(), m.end(), ay, None, "year", raw=raw))
            else:
                # "2019-20" with a non-consecutive pair: treat first year only
                toks.append(_Tok("point", m.start(), m.start() + 4, ay, None, "year", raw=raw[:4]))
        elif m.group("nmy"):
            toks.append(_Tok("point", m.start(), m.end(), int(m.group("ny")), int(m.group("nm")), "month", raw=raw))
        elif m.group("yr"):
            toks.append(_Tok("point", m.start(), m.end(), int(m.group("y")), None, "year", raw=raw))
        elif m.group("pres"):
            toks.append(_Tok("pres", m.start(), m.end(), raw=raw))
    return toks


def _start_ym(t: _Tok, other_end_year: Optional[int]) -> int:
    if t.kind == "season":
        lo, hi = _SEASON_RANGE[t.season or "summer"]
        return ym(t.year or 0, lo if lo != 12 else 12)
    if t.month is not None:
        return ym(t.year or 0, t.month)
    # year only
    if other_end_year is not None and other_end_year == t.year:
        return ym(t.year or 0, 1)
    return ym(t.year or 0, 7)


def _end_ym(t: _Tok, start_is_year_only: bool) -> int:
    if t.kind == "season":
        lo, hi = _SEASON_RANGE[t.season or "summer"]
        return ym((t.year or 0) + (1 if hi < lo else 0), hi)
    if t.month is not None:
        return ym(t.year or 0, t.month)
    return ym(t.year or 0, 6 if start_is_year_only else 12)


def _pair_ok(text: str, t: "_Tok", nxt: "_Tok") -> bool:
    between = text[t.end:nxt.start]
    if nxt.kind == "pres":
        return (not between.strip()) or bool(_SEP_RE.match(between))
    if not between.strip():
        return False
    return bool(_SEP_RE.match(between))


def find_date_ranges(text: str) -> List[DateRange]:
    """Find all date ranges / points in ``text`` (left to right, non-overlapping)."""
    toks = _tokens(text)
    # month-only token inherits the year of the next token: "Jan - Mar 2020"
    for i, t in enumerate(toks):
        if t.kind == "mo_only" and i + 1 < len(toks) and toks[i + 1].kind == "point":
            t.year = toks[i + 1].year
            t.kind = "point"
    toks = [t for t in toks if not (t.kind == "mo_only")]
    out: List[DateRange] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "pres":
            i += 1
            continue
        if t.kind == "acad":
            ay = t.year or 0
            out.append(DateRange(ym(ay, 7), ym(ay + 1, 6), False, (t.start, t.end), t.raw, "range", "year", "year", 0.7))
            i += 1
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if nxt is not None and nxt.kind != "acad" and _pair_ok(text, t, nxt):
            if nxt.kind == "pres":
                s_prec = t.prec
                s = _start_ym(t, None)
                out.append(DateRange(s, None, True, (t.start, nxt.end), text[t.start:nxt.end], "range", s_prec, "month",
                                     1.0 if s_prec == "month" else 0.75))
            else:
                s_year_only = t.prec == "year"
                e_year_only = nxt.prec == "year"
                s = _start_ym(t, nxt.year if e_year_only else None)
                e = _end_ym(nxt, s_year_only)
                conf = 1.0
                if s_year_only or e_year_only:
                    conf = 0.75
                if t.kind == "season" or nxt.kind == "season":
                    conf = min(conf, 0.6)
                out.append(DateRange(s, e, False, (t.start, nxt.end), text[t.start:nxt.end], "range", t.prec, nxt.prec, conf))
            i += 2
            continue
        if t.kind == "season":
            lo, hi = _SEASON_RANGE[t.season or "summer"]
            s = ym(t.year or 0, lo)
            e = ym((t.year or 0) + (1 if hi < lo else 0), hi)
            out.append(DateRange(s, e, False, (t.start, t.end), t.raw, "season", "season", "season", 0.5))
        else:
            s = _start_ym(t, None) if t.month is not None else ym(t.year or 0, 7)
            out.append(DateRange(s, None, False, (t.start, t.end), t.raw, "point", t.prec, t.prec, 0.4))
        i += 1
    return out


def strip_ranges(text: str, ranges: Iterable[DateRange]) -> str:
    """Remove the matched date text (and now-empty brackets / dangling separators)."""
    out = text
    for r in sorted(ranges, key=lambda r: -r.span[0]):
        out = out[: r.span[0]] + " " + out[r.span[1]:]
    out = re.sub(r"\(\s*\)|\[\s*\]", " ", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip(" \t|,;-–—·•(")


# --------------------------------------------------------------------------- durations

_DUR_RE = re.compile(
    r"(?<![\w.])(?:(?P<y>\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?|y)\b)?[\s,and]*(?:(?P<m>\d+)\s*\+?\s*(?:months?|mos?|m)\b)?",
    re.I,
)


def parse_duration(text: str) -> Optional[int]:
    """'1.5 years', '2 yrs 3 mos', '6 months' -> months. ``None`` if no duration found."""
    best = None
    for m in re.finditer(
        r"(?<![\w.])(\d+(?:\.\d+)?)\s*\+?\s*(years?|yrs?)\b(?:\s*(?:and|,|&)?\s*(\d{1,2})\s*(months?|mos?)\b)?", text, re.I
    ):
        months = int(round(float(m.group(1)) * 12)) + (int(m.group(3)) if m.group(3) else 0)
        best = months if best is None else best + months
    if best is None:
        m = re.search(r"(?<![\w.])(\d{1,3})\s*\+?\s*(months?|mos)\b", text, re.I)
        if m:
            best = int(m.group(1))
    return best


# --------------------------------------------------------------------------- intervals

def merge_intervals(intervals: Iterable[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Merge inclusive month intervals; adjacent months (end+1 == start) are merged too."""
    iv = sorted((a, b) for a, b in intervals if a is not None and b is not None and a <= b)
    out: List[Tuple[int, int]] = []
    for a, b in iv:
        if out and a <= out[-1][1] + 1:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def union_months(intervals: Iterable[Tuple[int, int]]) -> int:
    """Total months covered by the union of inclusive intervals."""
    return sum(b - a + 1 for a, b in merge_intervals(intervals))


def gaps(intervals: Iterable[Tuple[int, int]]) -> List[Tuple[int, int, int]]:
    """Gaps between merged intervals as ``(gap_start, gap_end, months)`` (inclusive)."""
    merged = merge_intervals(intervals)
    return [(a1 + 1, b0 - 1, b0 - a1 - 1) for (a0, a1), (b0, b1) in zip(merged, merged[1:]) if b0 - a1 - 1 > 0]
