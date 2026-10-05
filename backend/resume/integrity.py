"""Integrity analysis: neutral, reviewable *signals* (never verdicts).

Flag catalogue (code -> severity):

=============================  ========================  ======================================
code                           severity                  meaning
=============================  ========================  ======================================
hidden_text                    low / medium / high       invisible text (white, tiny, off-page,
                                                         DOCX vanish); high when > 50 chars
prompt_injection_text          high                      text addressed to an AI screener
keyword_stuffing               medium / high             giant skill list, repeated tokens,
                                                         skills repeated without sentence context
timeline_overlap_fulltime      low                       two full-time roles at different
                                                         companies overlap > 3 months
timeline_future_dates          low / medium              role dates in the future
start_after_end                medium                    a role ends before it starts
implausible_experience         medium                    role starts long before the earliest
                                                         listed education finished, or total
                                                         experience exceeds the time available
employment_gap                 info                      > 12 months between roles
skill_without_evidence         info / low                skills only named in a skills list
too_many_jobs_short_tenure     info / low                many roles shorter than 12 months
duplicate_content              info / low                same bullet reused across roles
contact_missing                info / low / medium       no e-mail and/or phone found
template_text                  low / medium              unfilled template placeholders
pdf_active_content             info .. high              JS / launch / embedded files in the PDF
metadata_anomaly               low / medium              inconsistent document metadata
non_printable_chars            info / medium             control / replacement characters
experience_claim_mismatch      low                       "10+ years" claim vs dated roles
extension_mismatch             info                      file extension differs from content
truncated_pages                info                      only the first N pages analysed
=============================  ========================  ======================================

Flags are phrased neutrally; many have innocent explanations.  No protected
attribute (age, gender, ethnicity, nationality, ...) is inferred anywhere: the
plausibility checks compare *role dates with education dates* and never
produce or store an age.

Fingerprint
    ``Integrity.fingerprint`` carries ``text_sha256`` (exact) and a 64-bit
    SimHash over normalised word 3-shingles for near-duplicate detection:
    ``similarity(fp_a, fp_b)`` is ``1 - hamming/64``.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .dates import gaps, merge_intervals
from .experience import valid_interval
from .models import EducationEntry, ExperienceEntry
from .privacy import find_injection_phrases
from .util import current_ym, norm_key, ym_str

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
_RISK_WEIGHT = {"high": 0.45, "medium": 0.2, "low": 0.07, "info": 0.0}


@dataclass
class Flag:
    code: str
    severity: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Fingerprint:
    simhash: str = ""
    text_sha256: str = ""
    shingles: int = 0


@dataclass
class Integrity:
    flags: List[Flag] = field(default_factory=list)
    risk_score: float = 0.0
    fingerprint: Fingerprint = field(default_factory=Fingerprint)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def has(self, code: str) -> bool:
        return any(f.code == code for f in self.flags)

    def get(self, code: str) -> Optional[Flag]:
        return next((f for f in self.flags if f.code == code), None)


# --------------------------------------------------------------------------- fingerprint

def _h64(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


def simhash64(text: str, shingle: int = 3) -> int:
    toks = norm_key(text).split()
    if not toks:
        return 0
    grams = [" ".join(toks[i:i + shingle]) for i in range(max(1, len(toks) - shingle + 1))]
    counts = Counter(grams)
    vec = [0] * 64
    for g, w in counts.items():
        h = _h64(g)
        for b in range(64):
            vec[b] += w if (h >> b) & 1 else -w
    out = 0
    for b in range(64):
        if vec[b] > 0:
            out |= 1 << b
    return out


def text_sha256(text: str) -> str:
    return hashlib.sha256(norm_key(text).encode("utf-8")).hexdigest()


def make_fingerprint(text: str) -> Fingerprint:
    toks = norm_key(text).split()
    return Fingerprint(simhash=f"{simhash64(text):016x}", text_sha256=text_sha256(text), shingles=max(0, len(toks) - 2))


FP = Union[int, str, Fingerprint, Dict[str, Any]]


def _fp_int(fp: FP) -> int:
    if isinstance(fp, int):
        return fp
    if isinstance(fp, str):
        return int(fp, 16)
    if isinstance(fp, Fingerprint):
        return int(fp.simhash, 16)
    if isinstance(fp, dict):
        sub = fp.get("fingerprint", fp)
        return int(sub["simhash"], 16)
    if hasattr(fp, "integrity"):
        return int(fp.integrity.fingerprint.simhash, 16)
    raise TypeError("unsupported fingerprint type")


def similarity(fp_a: FP, fp_b: FP) -> float:
    """Similarity in [0, 1] between two fingerprints (1 - hamming/64)."""
    return 1.0 - bin(_fp_int(fp_a) ^ _fp_int(fp_b)).count("1") / 64.0


# --------------------------------------------------------------------------- checks

_TEMPLATE_PHRASES = [
    (r"lorem ipsum", "medium"), (r"\byour name\b", "low"), (r"\[\s*your", "low"), (r"insert (?:company|your|job|name)", "low"),
    (r"\bcompany name\b(?!s)", "low"), (r"job title here", "low"), (r"x{3}[-. ]x{3}[-. ]x{4}", "low"),
    (r"your\.?email@|youremail@|yourname@|name@(?:example|email|domain)", "low"), (r"\(?123\)?[- .]456[- .]7890", "low"),
    (r"city,\s*state\s*(?:zip|\d{5})", "low"), (r"\buniversity name\b", "low"), (r"\bmonth,?\s*year\b", "low"),
    (r"dates? of employment", "low"), (r"\bdescribe your\b", "low"), (r"\benter your\b", "low"), (r"street address", "low"),
    (r"\bmm/yyyy\b", "low"),
]
_TEMPLATE_RES = [(re.compile(p, re.I), sev) for p, sev in _TEMPLATE_PHRASES]
_CLAIM_RE = re.compile(
    r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+(?:[\w/&-]+\s+){0,3})?(?:experience|exp\b)", re.I
)
_LEVEL_OFFSET = {"high_school": 2, "diploma": 4, "associate": 4, "certificate": 2, "bachelor": 6, "master": 8,
                 "mba": 8, "doctorate": 10, "other": 4}


def claimed_experience_years(text: str) -> Optional[float]:
    vals = [float(m.group(1)) for m in _CLAIM_RE.finditer(text)]
    vals = [v for v in vals if 0 < v <= 50]
    return max(vals) if vals else None


def _chain(*flags: Optional[Flag]) -> List[Flag]:
    return [f for f in flags if f]


def check_hidden(hidden: Sequence[Dict[str, Any]]) -> Optional[Flag]:
    total = sum(len(h["text"]) for h in hidden)
    if total <= 0:
        return None
    sev = "high" if total > 50 else ("medium" if total >= 20 else "low")
    reasons = Counter(h["reason"] for h in hidden)
    sample = " ".join(h["text"] for h in hidden)[:200]
    return Flag(
        "hidden_text", sev,
        f"{total} characters of text that is not visible to a human reader (e.g. white, tiny or off-page text) "
        "were found; they were excluded from skill and experience extraction.",
        {"chars": total, "reasons": dict(reasons), "sample": sample},
    )


def check_injection(visible: str, hidden: Sequence[Dict[str, Any]]) -> Optional[Flag]:
    hid_text = " ".join(h["text"] for h in hidden)
    phrases = find_injection_phrases(visible + "\n" + hid_text)
    if not phrases:
        return None
    in_hidden = bool(find_injection_phrases(hid_text))
    return Flag(
        "prompt_injection_text", "high",
        "The document contains text that looks like instructions to an AI system (e.g. 'ignore previous "
        "instructions'). It is never forwarded to interviewer prompts unsanitised.",
        {"phrases": phrases, "in_hidden_text": in_hidden},
    )


def check_keyword_stuffing(text: str, skills: Sequence[Any], skill_stats: Sequence[Dict[str, Any]]) -> Optional[Flag]:
    reasons: List[str] = []
    details: Dict[str, Any] = {}
    sev = "medium"
    n_list = sum(1 for s in skills if "skills_section" in s.evidence_sources and not s.implied)
    if n_list > 60:
        reasons.append("very_long_skill_list")
        details["skills_in_list"] = n_list
        if n_list > 100:
            sev = "high"
    m = re.search(r"\b(\w{3,})\b(?:\W+\1\b){4,}", text, re.I)
    if m:
        reasons.append("repeated_token_run")
        details["token"] = m.group(1)[:30]
    heavy = [s["name"] for s in skill_stats if s["mentions"] >= 10 and s["in_sentence"] <= 0.2 * s["mentions"]]
    if heavy:
        reasons.append("skills_repeated_without_context")
        details["skills"] = heavy[:5]
    if not reasons:
        return None
    details["reasons"] = reasons
    return Flag("keyword_stuffing", sev,
                "Skill terms appear in unusually large numbers or without surrounding sentences.", details)


def _full_time(e: ExperienceEntry) -> bool:
    return e.employment_type == "full_time"


def check_timeline(entries: Sequence[ExperienceEntry]) -> List[Flag]:
    flags: List[Flag] = []
    # overlaps
    pairs = []
    ft = [(e, valid_interval(e)) for e in entries if _full_time(e)]
    ft = [(e, iv) for e, iv in ft if iv]
    for i in range(len(ft)):
        for j in range(i + 1, len(ft)):
            (a, ia), (b, ib) = ft[i], ft[j]
            if a.company and b.company and a.company.lower() == b.company.lower():
                continue
            ov = min(ia[1], ib[1]) - max(ia[0], ib[0]) + 1
            if ov > 3:
                pairs.append({"a": f"{a.title} @ {a.company}".strip(" @"), "b": f"{b.title} @ {b.company}".strip(" @"), "months": ov})
    if pairs:
        pairs.sort(key=lambda p: -p["months"])
        flags.append(Flag("timeline_overlap_fulltime", "low",
                          "Full-time roles at different organisations overlap by more than 3 months.",
                          {"pairs": pairs[:4], "count": len(pairs)}))
    fut_end = [e for e in entries if "future_end" in e.issues]
    fut_start = [e for e in entries if "future_start" in e.issues]
    if fut_start or fut_end:
        flags.append(Flag("timeline_future_dates", "medium" if fut_start else "low",
                          "One or more roles have dates in the future (clipped to today for calculations).",
                          {"roles": [f"{e.title} @ {e.company}".strip(" @") for e in fut_start + fut_end][:4]}))
    bad = [e for e in entries if "start_after_end" in e.issues]
    if bad:
        flags.append(Flag("start_after_end", "medium", "A role has an end date earlier than its start date; it was excluded from totals.",
                          {"roles": [f"{e.title} @ {e.company}".strip(" @") for e in bad][:4]}))
    return flags


def check_gaps(entries: Sequence[ExperienceEntry], min_gap_months: int = 12) -> Optional[Flag]:
    ivs = [iv for iv in (valid_interval(e) for e in entries) if iv]
    if not ivs:
        return None
    found = [(a, b, m) for a, b, m in gaps(ivs) if m > min_gap_months]
    merged = merge_intervals(ivs)
    trailing = current_ym() - merged[-1][1]
    items = [{"start": ym_str(a), "end": ym_str(b), "months": m} for a, b, m in found]
    if trailing > min_gap_months and not any(e.is_current for e in entries):
        items.append({"start": ym_str(merged[-1][1] + 1), "end": ym_str(current_ym()), "months": trailing, "since_last_role": True})
    if not items:
        return None
    return Flag("employment_gap", "info", "Periods of more than 12 months between listed roles. Often benign (study, care, travel, relocation).",
                {"gaps": items[:5]})


def check_implausible(entries: Sequence[ExperienceEntry], education: Sequence[EducationEntry], total_months: int) -> Optional[Flag]:
    dated = [e for e in entries if e.start_ym is not None]
    edu = [ed for ed in education if ed.end_year]
    if not dated or not edu:
        return None
    ref = min(edu, key=lambda ed: ed.end_year)
    threshold_year = ref.end_year - _LEVEL_OFFSET.get(ref.degree_level, 4)
    first = min(dated, key=lambda e: e.start_ym)
    problems = []
    if first.start_ym // 12 < threshold_year:
        problems.append("role_starts_long_before_education_end")
    avail_years = date.today().year - threshold_year + 1
    if total_months / 12 > avail_years + 0.5:
        problems.append("experience_exceeds_available_time")
    if not problems:
        return None
    return Flag("implausible_experience", "medium",
                "Role dates are hard to reconcile with the education timeline (roles begin long before the earliest listed "
                "education ended, or total experience exceeds the time available).",
                {"problems": problems, "earliest_role_start": first.start, "earliest_education_end_year": ref.end_year,
                 "education_level": ref.degree_level, "total_experience_years": round(total_months / 12, 1)})


def check_short_tenure(entries: Sequence[ExperienceEntry]) -> Optional[Flag]:
    jobs = [e for e in entries if _full_time(e) and valid_interval(e)]
    short = [e for e in jobs if e.duration_months and e.duration_months < 12 and not e.is_current]
    if len(short) < 4:
        return None
    return Flag("too_many_jobs_short_tenure", "low" if len(short) >= 6 else "info",
                f"{len(short)} of {len(jobs)} full-time roles lasted under 12 months.",
                {"short_roles": len(short), "total_roles": len(jobs)})


def check_duplicates(entries: Sequence[ExperienceEntry]) -> Optional[Flag]:
    seen: Dict[str, int] = {}
    dups: List[str] = []
    for ei, e in enumerate(entries):
        local = set()
        for b in e.bullets:
            k = norm_key(b)
            if len(k) < 40 or k in local:
                continue
            local.add(k)
            if k in seen and seen[k] != ei:
                dups.append(b[:100])
            else:
                seen[k] = ei
    if not dups:
        return None
    return Flag("duplicate_content", "low" if len(dups) >= 3 else "info",
                "The same bullet text is reused across different roles.", {"count": len(dups), "sample": dups[:2]})


def check_skill_evidence(skills: Sequence[Any], has_experience_text: bool) -> Optional[Flag]:
    if not has_experience_text:
        return None
    explicit = [s for s in skills if not s.implied]
    if len(explicit) < 8:
        return None
    only = [s for s in explicit if not (set(s.evidence_sources) & {"experience", "projects", "certifications"})]
    ratio = len(only) / len(explicit)
    if ratio < 0.6:
        return None
    sev = "low" if (ratio >= 0.8 and len(only) >= 15) else "info"
    return Flag("skill_without_evidence", sev,
                f"{len(only)} of {len(explicit)} skills appear only in a skills list, never in a role, project or certification.",
                {"count": len(only), "of": len(explicit), "sample": [s.name for s in only[:8]]})


def check_contact(email: str, phone: str) -> Optional[Flag]:
    missing = [n for n, v in (("email", email), ("phone", phone)) if not v]
    if not missing:
        return None
    sev = "medium" if len(missing) == 2 else ("low" if "email" in missing else "info")
    return Flag("contact_missing", sev, "No " + " or ".join(missing) + " was found.", {"missing": missing})


def check_template(text: str) -> Optional[Flag]:
    hits = []
    sev = "low"
    for rx, s in _TEMPLATE_RES:
        m = rx.search(text)
        if m:
            hits.append(m.group(0)[:40])
            if s == "medium":
                sev = "medium"
    if not hits:
        return None
    if len(hits) >= 3:
        sev = "medium"
    return Flag("template_text", sev, "Unfilled template placeholders were found.", {"phrases": hits[:5]})


def _iso(s: str) -> Optional[date]:
    try:
        y, m, d = (int(x) for x in (s + "-01-01").split("-")[:3])
        return date(y, m, d)
    except Exception:
        return None


def check_metadata(md: Dict[str, Any]) -> Optional[Flag]:
    if not md:
        return None
    created, modified = _iso(md.get("created") or ""), _iso(md.get("modified") or "")
    issues = []
    sev = "low"
    today = date.today()
    if created and created > today:
        issues.append("creation_date_in_future")
        sev = "medium"
    if modified and modified > today:
        issues.append("modification_date_in_future")
        sev = "medium"
    if created and modified and modified < created:
        issues.append("modified_before_created")
    if not issues:
        return None
    return Flag("metadata_anomaly", sev, "Document metadata dates are inconsistent.",
                {"issues": issues, "producer": md.get("producer", ""), "creator": md.get("creator", ""),
                 "created": md.get("created", ""), "modified": md.get("modified", "")})


def check_nonprintable(count: int, raw_chars: int) -> Optional[Flag]:
    if count < 5:
        return None
    ratio = count / max(raw_chars, 1)
    return Flag("non_printable_chars", "medium" if ratio > 0.05 else "info",
                "Control, replacement or unmapped glyph characters were found (garbled fonts or encoding problems).",
                {"count": count, "ratio": round(ratio, 4)})


def check_claim(text: str, total_months: int, has_dates: bool) -> Optional[Flag]:
    claim = claimed_experience_years(text)
    if claim is None or not has_dates:
        return None
    computed = total_months / 12
    if claim - computed >= 2.5:
        return Flag("experience_claim_mismatch", "low",
                    f"The text claims about {claim:g} years of experience; dated roles add up to {computed:.1f}.",
                    {"claimed_years": claim, "computed_years": round(computed, 1)})
    return None


def build_integrity(
    *,
    text: str,
    hidden: Sequence[Dict[str, Any]],
    experience: Sequence[ExperienceEntry],
    education: Sequence[EducationEntry],
    skills: Sequence[Any],
    skill_stats: Sequence[Dict[str, Any]],
    email: str,
    phone: str,
    total_months: int,
    metadata: Dict[str, Any],
    nonprintable: int,
    raw_chars: int,
    extra_flags: Sequence[Dict[str, Any]] = (),
) -> Integrity:
    flags: List[Flag] = []
    flags += _chain(
        check_hidden(hidden),
        check_injection(text, hidden),
        check_keyword_stuffing(text, skills, skill_stats),
    )
    flags += check_timeline(experience)
    flags += _chain(
        check_gaps(experience),
        check_implausible(experience, education, total_months),
        check_short_tenure(experience),
        check_duplicates(experience),
        check_skill_evidence(skills, any(e.bullets for e in experience)),
        check_contact(email, phone),
        check_template(text),
        check_metadata(metadata),
        check_nonprintable(nonprintable, raw_chars),
        check_claim(text, total_months, any(valid_interval(e) for e in experience)),
    )
    for f in extra_flags:
        flags.append(Flag(f["code"], f["severity"], f["message"], f.get("details", {})))
    flags.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.code))
    keep = 1.0
    for f in flags:
        keep *= 1.0 - _RISK_WEIGHT.get(f.severity, 0.0)
    return Integrity(
        flags=flags,
        risk_score=round(1.0 - keep, 3),
        fingerprint=make_fingerprint(text),
        metadata={k: v for k, v in (metadata or {}).items() if k in ("producer", "creator", "created", "modified", "author", "title", "last_modified_by", "pages")},
    )
