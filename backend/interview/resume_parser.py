"""Backward-compatible facade over the offline ``resume`` engine.

``ResumeParser().parse(path) -> dict`` still returns the legacy keys
(``raw_text, name, email, phone, skills, education, work_history,
experience_years, links``) and adds ``engine`` (the full
``resume.ParsedResume.to_dict()`` result), ``integrity`` and ``probe_plan``.

* Missing file            -> ``FileNotFoundError``
* Empty / unreadable file -> ``ValueError`` (``resume.EmptyResume`` is a ``ValueError``)
* Unsupported / hostile   -> ``ValueError`` subclasses (``UnsupportedFormat``, ``MaliciousFile``...)

Pass ``ResumeParser(isolated=True)`` to run each parse in a throw-away spawned process with a hard
timeout (recommended for untrusted uploads served by a web worker).
No LLM, no network, no spaCy.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Union

from resume import parse_resume, parse_resume_isolated


def _fmt_duration(e: Dict[str, Any]) -> str:
    start, end = e.get("start"), e.get("end")
    if start and (end or e.get("is_current")):
        return f"{start} - {'Present' if e.get('is_current') else end}"
    return e.get("date_text") or (start or "")


def to_legacy(engine: Dict[str, Any], raw_text: str = "") -> Dict[str, Any]:
    """Map the engine's ``to_dict()`` output onto the legacy parser result shape."""
    contact = engine.get("contact", {})
    skills: List[str] = []
    for s in list(engine.get("skills", [])) + list(engine.get("soft_skills", [])):
        if s["name"] not in skills:
            skills.append(s["name"])
    education = []
    for ed in engine.get("education", []):
        degree = ed.get("degree_raw", "")
        if ed.get("field"):
            degree = f"{degree} in {ed['field']}".strip()
        education.append({
            "degree": degree,
            "institution": ed.get("institution", ""),
            "year": str(ed.get("end_year") or ed.get("start_year") or ""),
            "field": ed.get("field", ""),
            "level": ed.get("degree_level", ""),
            "gpa": ed.get("gpa"),
            "gpa_scale": ed.get("gpa_scale"),
        })
    work_history = []
    for e in engine.get("experience", []):
        work_history.append({
            "title": e.get("title", ""),
            "company": e.get("company", ""),
            "duration": _fmt_duration(e),
            "description": "\n".join(e.get("bullets", [])),
            "location": e.get("location", ""),
            "start": e.get("start"),
            "end": e.get("end"),
            "is_current": e.get("is_current", False),
            "duration_months": e.get("duration_months", 0),
            "employment_type": e.get("employment_type", ""),
            "skills": e.get("skills", []),
        })
    links = [{"url": l["url"], "type": l["type"], "label": l.get("label", "")} for l in contact.get("links", [])]
    engine_no_text = {k: v for k, v in engine.items() if k != "text"}
    return {
        "raw_text": raw_text,
        "name": contact.get("name", ""),
        "email": contact.get("email", ""),
        "phone": contact.get("phone", ""),
        "skills": skills,
        "education": education,
        "work_history": work_history,
        "experience_years": round(engine.get("total_experience_months", 0) / 12.0, 1),
        "links": links,
        "engine": engine_no_text,
        "integrity": engine.get("integrity"),
        "probe_plan": engine.get("probe_plan"),
    }


class ResumeParser:
    """Drop-in replacement for the former regex parser."""

    def __init__(self, isolated: bool = False, default_region: str = "US", time_budget_s: float = 20.0):
        self.isolated = isolated
        self.default_region = default_region
        self.time_budget_s = time_budget_s

    def parse(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Resume file not found: {path}")
        if self.isolated:
            engine = parse_resume_isolated(
                path, path.name, default_region=self.default_region, time_budget_s=self.time_budget_s, include_text=True,
            )
            return to_legacy(engine, engine.get("text", ""))
        parsed = parse_resume(path, path.name, default_region=self.default_region, time_budget_s=self.time_budget_s)
        return to_legacy(parsed.to_dict(), parsed.text)
