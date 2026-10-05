"""The orchestration layer: ``parse_resume`` and ``parse_resume_isolated``."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from . import contact as contact_mod
from .dates import merge_intervals, union_months
from .education import parse_education
from .errors import EmptyResume, ParseTimeout, ResumeParseError
from .experience import parse_experience, seniority_from, valid_interval
from .extract import ExtractedDoc, Line, extract_docx, extract_pdf, extract_txt, lines_to_text
from .extras import build_summary, fallback_summary, parse_certifications, parse_languages, parse_projects
from .integrity import build_integrity, make_fingerprint
from .models import (
    SCHEMA_VERSION, Contact, EducationEntry, ExperienceEntry, Link, ParsedResume, SourceInfo,
)
from .plan import build_probe_plan
from .privacy import strip_protected
from .sections import Section, segment, split_inline_labels
from .skills import Hit, aggregate, closure, get_taxonomy, match_lines
from .util import Deadline
from .validate import Limits, sniff_and_validate

BytesLike = Union[bytes, bytearray, memoryview, str, Path]
_STRICT = lambda: os.environ.get("RESUME_STRICT") == "1"   # noqa: E731  (tests re-raise stage errors)

_IGNORE_FOR_SKILLS = {"references", "declaration", "personal", "interests"}
_IGNORE_FOR_CONTACT = {"references", "declaration"}


def _stage(name: str, warnings: List[str], default: Any, fn: Callable[[], Any], deadline: Deadline) -> Any:
    deadline.check(name)
    try:
        return fn()
    except (ParseTimeout, ResumeParseError):
        raise
    except Exception:
        if _STRICT():
            raise
        warnings.append(f"stage_failed:{name}")
        return default


def _line_sections(n: int, sections: Sequence[Section]) -> List[str]:
    out = ["header"] * n
    for s in sections:
        for i in range(s.start, min(s.end, n)):
            out[i] = s.name
    return out


def _region(lines: Sequence[Line], sections: Sequence[Section], names: Sequence[str]) -> List[List[Tuple[int, Line]]]:
    regions = []
    for s in sections:
        if s.name in names:
            r = [(i, lines[i]) for i in range(s.content_start, s.end)]
            if r:
                regions.append(r)
    return regions


def _relabel_languages(lines: Sequence[Line], sections: List[Section]) -> None:
    for s in sections:
        if s.name == "languages":
            reg = [(i, lines[i]) for i in range(s.content_start, s.end)]
            if reg and not parse_languages(reg):
                s.name = "skills"


def parse_resume(
    path_or_bytes: BytesLike,
    filename: Optional[str] = None,
    *,
    time_budget_s: float = 20.0,
    ocr: bool = True,
    default_region: str = "US",
    limits: Optional[Limits] = None,
) -> ParsedResume:
    """Parse a PDF / DOCX / TXT resume into a :class:`ParsedResume`.

    Raises :class:`~resume.errors.ResumeParseError` subclasses for hard failures;
    sub-stage failures never raise (they add a ``stage_failed:<stage>`` warning).
    """
    deadline = Deadline(time_budget_s)
    limits = limits or Limits()
    warnings: List[str] = []

    sn = sniff_and_validate(path_or_bytes, filename, limits)
    deadline.check("validate")
    if sn.fmt == "pdf":
        doc: ExtractedDoc = extract_pdf(sn.data, limits, deadline, ocr=ocr)
    elif sn.fmt == "docx":
        doc = extract_docx(sn.data, limits, deadline)
    else:
        doc = extract_txt(sn.data)
    deadline.check("extract")
    warnings.extend(w for w in doc.warnings if w not in warnings)

    # ---- bounds, protected attributes, inline labels, sections --------------------------
    bounded: List[Line] = []
    total_chars = 0
    for ln in doc.lines:
        if len(ln.text) > limits.max_line_chars:
            ln.text = ln.text[: limits.max_line_chars]
            if "line_truncated" not in warnings:
                warnings.append("line_truncated")
        total_chars += len(ln.text)
        if total_chars > limits.max_text_chars:
            warnings.append("text_truncated")
            break
        bounded.append(ln)
    lines, redacted = strip_protected(bounded)
    if doc.has_photo and "photo" not in redacted:
        redacted.append("photo")
    lines = split_inline_labels(lines)
    if not lines or sum(len(l.text) for l in lines) < 8:
        raise EmptyResume("the document contains no readable text")
    sections = segment(lines)
    _relabel_languages(lines, sections)
    n = len(lines)
    line_sec = _line_sections(n, sections)
    text = lines_to_text(lines)
    fp = make_fingerprint(text)
    first_heading = next((s.start for s in sections if s.name != "header"), 0)
    if redacted:
        warnings.append("protected_attributes_removed")
    if doc.hidden:
        warnings.append("hidden_text_removed")

    # ---- contact ------------------------------------------------------------------------
    header_zone = {i for i in range(n) if line_sec[i] in ("header", "contact") and i < max(first_heading, 12) + 2}
    header_zone |= set(range(min(n, 8)))

    def contact_stage() -> Contact:
        allowed = [i for i in range(n) if line_sec[i] not in _IGNORE_FOR_CONTACT]
        emails: List[str] = []
        hz_text = "\n".join(lines[i].text for i in sorted(header_zone) if i in set(allowed))
        for e in contact_mod.find_emails(hz_text):
            if e not in emails:
                emails.append(e)
        for e in contact_mod.find_emails("\n".join(lines[i].text for i in allowed)):
            if e not in emails:
                emails.append(e)
        tax = get_taxonomy()
        links, mails, tels = contact_mod.find_links(
            lines, doc.links, lambda i: i in header_zone, lambda i: line_sec[i] in _IGNORE_FOR_CONTACT,
            taxonomy_surfaces=set(tax.surfaces),
        )
        for e in mails:
            v = contact_mod._valid_email(e)
            if v and v not in emails:
                emails.append(v)
        phones = contact_mod.find_phones(hz_text, default_region)
        if not phones:
            phones = contact_mod.find_phones("\n".join(lines[i].text for i in allowed), default_region)
        for t in tels:
            for p in contact_mod.find_phones(t if t.startswith("+") else "+" + t.lstrip("+"), default_region):
                if all(p.e164 != q.e164 for q in phones):
                    phones.append(p)
        name, conf = contact_mod.extract_name(lines, first_heading, emails, links)
        loc = contact_mod.find_location(lines, sorted(header_zone))
        return Contact(
            name=name, name_confidence=conf, email=emails[0] if emails else "", emails=emails,
            phone=phones[0].e164 if phones else "", phones=phones, location=loc, links=links,
        )

    contact = _stage("contact", warnings, Contact(), contact_stage, deadline)

    # ---- skills -------------------------------------------------------------------------
    skill_texts = ["" if line_sec[i] in _IGNORE_FOR_SKILLS else lines[i].text for i in range(n)]
    hits: List[Hit] = _stage("skills", warnings, [], lambda: match_lines(skill_texts, line_sec), deadline)
    agg = aggregate(hits, line_sec) if hits else None
    hits_by_line: Dict[int, List[Hit]] = {}
    for h in hits:
        hits_by_line.setdefault(h.line, []).append(h)
    tax = get_taxonomy()

    def names_for_lines(a: int, b: int) -> List[str]:
        out: List[str] = []
        for li in range(a, min(b, n)):
            for h in hits_by_line.get(li, ()):
                nm = tax.skills[h.skill]["name"]
                if tax.skills[h.skill]["category"] != "soft_skill" and nm not in out:
                    out.append(nm)
        return out

    def idx_for_lines(a: int, b: int) -> Set[int]:
        return {h.skill for li in range(a, min(b, n)) for h in hits_by_line.get(li, ()) if tax.skills[h.skill]["category"] != "soft_skill"}

    # ---- experience ---------------------------------------------------------------------
    def experience_stage() -> List[ExperienceEntry]:
        entries: List[ExperienceEntry] = []
        regs = _region(lines, sections, ["experience"])
        fb = False
        if not regs:
            fb = True
            reg = [(i, lines[i]) for i in range(n) if line_sec[i] == "header"]
            regs = [reg] if reg else []
        for reg in regs:
            sec_heading = next((s.heading for s in sections if s.start <= reg[0][0] <= s.end and s.name == "experience"), "")
            es, w = parse_experience(reg, section_heading=sec_heading, fallback=fb)
            entries.extend(es)
            warnings.extend(x for x in w if x not in warnings)
        if fb and entries:
            warnings.append("experience_section_not_found")
            for e in entries:
                e.confidence = round(e.confidence * 0.8, 3)
        today_clip = None
        for e in entries:
            if e.line_range:
                a, b = e.line_range
                e.skills = names_for_lines(a, b)
                e.skill_idx = closure(idx_for_lines(a, b), tax)
        return entries

    experience: List[ExperienceEntry] = _stage("experience", warnings, [], experience_stage, deadline)

    # ---- education / projects / certs / languages / summary ---------------------------------
    def education_stage() -> List[EducationEntry]:
        out: List[EducationEntry] = []
        for reg in _region(lines, sections, ["education"]):
            out.extend(parse_education(reg))
        if not out:
            reg = [(i, lines[i]) for i in range(n) if line_sec[i] in ("header", "summary", "awards", "personal")]
            out = parse_education(reg, fallback=True)
        return out

    education = _stage("education", warnings, [], education_stage, deadline)

    projects = _stage("projects", warnings, [], lambda: [
        p for reg in _region(lines, sections, ["projects"]) for p in parse_projects(reg, names_for_lines)
    ], deadline)
    certifications = _stage("certifications", warnings, [], lambda: [
        c for reg in _region(lines, sections, ["certifications"]) for c in parse_certifications(reg)
    ], deadline)
    languages = _stage("languages", warnings, [], lambda: [
        l for reg in _region(lines, sections, ["languages"]) for l in parse_languages(reg)
    ], deadline)

    def summary_stage() -> str:
        idxs = [i for s in sections if s.name == "summary" for i in range(s.content_start, s.end)]
        if idxs:
            return build_summary(lines, idxs)
        return fallback_summary(lines, [i for i in range(n) if line_sec[i] == "header"])

    summary = _stage("summary", warnings, "", summary_stage, deadline)

    # ---- derived: totals, per-skill months, seniority --------------------------------------
    ivs = [iv for iv in (valid_interval(e) for e in experience) if iv]
    dated_months = union_months(ivs)
    undated = sum(e.duration_months for e in experience if e.date_precision == "duration")
    total_months = dated_months + undated
    ordered = sorted(experience, key=lambda e: ((1 if e.is_current else 0), e.end_ym if e.end_ym is not None else (e.start_ym or 0)), reverse=True)
    seniority = seniority_from(total_months, [e.title for e in ordered])

    skills_out = agg.skills if agg else []
    soft_out = agg.soft_skills if agg else []
    if agg:
        for idx, sk in agg.by_index.items():
            iv_s = [valid_interval(e) for e in experience if idx in e.skill_idx]
            sk.experience_months = union_months([iv for iv in iv_s if iv])
        # skills for entries come from experience stage; projects-only skills keep 0 months

    skill_stats: List[Dict[str, Any]] = []
    if agg:
        per: Dict[int, List[int]] = {}
        for h in hits:
            tot, sent = per.setdefault(h.skill, [0, 0])
            per[h.skill][0] += 1
            if line_sec[h.line] != "skills" and len(lines[h.line].text.split()) >= 6:
                per[h.skill][1] += 1
        skill_stats = [{"name": tax.skills[i]["name"], "mentions": t, "in_sentence": s} for i, (t, s) in per.items()]

    # ---- assemble ---------------------------------------------------------------------------
    extra_flags = [*sn.flags, *doc.flags]
    parsed = ParsedResume(
        source=SourceInfo(
            format=sn.fmt, pages=doc.pages, bytes=len(sn.data), sha256=sn.sha256, text_sha256=fp.text_sha256,
            extraction_method=doc.method, ocr_used=doc.ocr_used,
        ),
        contact=contact, summary=summary, skills=skills_out, soft_skills=soft_out, experience=experience,
        education=education, projects=projects, certifications=certifications, languages=languages,
        total_experience_months=total_months, total_experience_years=round(total_months / 12, 2),
        seniority_estimate=seniority, warnings=warnings,
        sections_detected=[s.to_dict() for s in sections], redacted_fields=redacted, text=text,
    )

    def integrity_stage() -> Any:
        full_text_for_injection = text
        integ = build_integrity(
            text=full_text_for_injection, hidden=doc.hidden, experience=experience, education=education,
            skills=skills_out, skill_stats=skill_stats, email=contact.email, phone=contact.phone,
            total_months=total_months, metadata=doc.metadata, nonprintable=doc.nonprintable,
            raw_chars=doc.raw_chars, extra_flags=extra_flags,
        )
        # de-duplicate flag codes coming from several sources
        seen: Set[Tuple[str, str]] = set()
        uniq = []
        for f in integ.flags:
            k = (f.code, f.severity)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(f)
        integ.flags = uniq
        return integ

    parsed.integrity = _stage("integrity", warnings, None, integrity_stage, deadline)
    if parsed.integrity is not None and parsed.integrity.has("timeline_overlap_fulltime"):
        warnings.append("timeline_overlap_fulltime")
    if not contact.name:
        warnings.append("no_contact_name")
    if not experience:
        warnings.append("no_experience_found")
    parsed.probe_plan = _stage("probe_plan", warnings, None, lambda: build_probe_plan(parsed), deadline)

    # ---- confidence -------------------------------------------------------------------------
    fc: Dict[str, float] = {}
    fc["name"] = contact.name_confidence
    fc["email"] = 1.0 if contact.email else 0.0
    fc["phone"] = 1.0 if contact.phone else 0.0
    fc["experience"] = (sum(e.confidence for e in experience) / len(experience)) if experience else (0.5 if (projects and seniority == "fresher") else 0.0)
    fc["education"] = (sum(e.confidence for e in education) / len(education)) if education else 0.0
    if skills_out:
        explicit = [s for s in skills_out if not s.implied]
        backed = sum(1 for s in explicit if set(s.evidence_sources) - {"skills_section"})
        fc["skills"] = round(min(1.0, len(explicit) / 6) * (0.6 + 0.4 * (backed / max(len(explicit), 1))), 3)
    else:
        fc["skills"] = 0.0
    fc["summary"] = 0.8 if summary else 0.0
    core = {s.name for s in sections} & {"experience", "education", "skills", "summary", "projects"}
    fc["sections"] = min(1.0, len(core) / 3)
    weights = {"name": 0.12, "email": 0.1, "phone": 0.05, "experience": 0.28, "education": 0.15, "skills": 0.2, "sections": 0.1}
    parsed.field_confidence = {k: round(v, 3) for k, v in fc.items()}
    parsed.overall_confidence = round(sum(fc[k] * w for k, w in weights.items()) / sum(weights.values()), 3)
    parsed.warnings = list(dict.fromkeys(parsed.warnings))
    return parsed


# --------------------------------------------------------------------------- isolation

def _isolated_worker(payload: Tuple[Any, Optional[str], Dict[str, Any]]) -> Dict[str, Any]:
    """Runs in the child process (module level so it is picklable under ``spawn``)."""
    src, filename, kwargs = payload
    limits = kwargs.pop("limits_dict", None)
    if limits:
        kwargs["limits"] = Limits(**limits)
    include_text = kwargs.pop("include_text", False)
    return parse_resume(src, filename, **kwargs).to_dict(include_text=include_text)


def _set_rlimit(max_memory_mb: Optional[int]) -> None:
    if not max_memory_mb:
        return
    try:
        import resource

        limit = int(max_memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except Exception:
        pass


def parse_resume_isolated(
    path_or_bytes: BytesLike,
    filename: Optional[str] = None,
    *,
    hard_timeout_s: float = 30.0,
    max_memory_mb: Optional[int] = 3072,
    time_budget_s: float = 20.0,
    ocr: bool = True,
    default_region: str = "US",
    limits: Optional[Limits] = None,
    include_text: bool = False,
    _target: Optional[Callable[[Any], Any]] = None,
) -> Dict[str, Any]:
    """Run :func:`parse_resume` in a short-lived *spawned* worker process and return ``to_dict()``.

    A hostile or pathological PDF can therefore neither hang nor crash (segfault, OOM) the calling
    process: after ``hard_timeout_s`` the worker is killed and :class:`ParseTimeout` is raised; a crashed
    worker raises :class:`ResumeParseError`.  Documented exceptions of :func:`parse_resume` propagate
    unchanged.  Safe to call from Django views / Celery tasks (spawn, not fork, so no inherited DB
    connections or locks); each call pays ~0.2-0.5 s of interpreter start-up, so use it for
    untrusted uploads and keep ``parse_resume`` for trusted/batch use.
    """
    import concurrent.futures as cf
    import multiprocessing as mp
    from concurrent.futures.process import BrokenProcessPool

    if isinstance(path_or_bytes, (str, Path)):
        # read in the parent (size-limited) so the child never needs filesystem access to the path
        from .validate import load_bytes

        lim = limits or Limits()
        if filename is None:
            filename = Path(path_or_bytes).name
        path_or_bytes = load_bytes(path_or_bytes, lim)
    kwargs: Dict[str, Any] = {"time_budget_s": time_budget_s, "ocr": ocr, "default_region": default_region,
                              "include_text": include_text}
    if limits:
        kwargs["limits_dict"] = limits.__dict__.copy()
    ctx = mp.get_context("spawn")
    ex = cf.ProcessPoolExecutor(max_workers=1, mp_context=ctx, initializer=_set_rlimit, initargs=(max_memory_mb,))
    try:
        fut = ex.submit(_target or _isolated_worker, (bytes(path_or_bytes), filename, kwargs))
        try:
            return fut.result(timeout=hard_timeout_s)
        except cf.TimeoutError:
            raise ParseTimeout(f"parser worker exceeded the hard timeout of {hard_timeout_s:.0f}s") from None
        except BrokenProcessPool:
            raise ResumeParseError("parser worker crashed while reading this file") from None
    finally:
        procs = list(getattr(ex, "_processes", {}).values())
        ex.shutdown(wait=False, cancel_futures=True)
        for p in procs:
            try:
                if p.is_alive():
                    p.kill()
            except Exception:
                pass
