"""Result schema (dataclasses, JSON-serialisable through ``to_dict``)."""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .skills import Skill

SCHEMA_VERSION = "1.0"


def to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses / containers into plain JSON types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: to_jsonable(getattr(obj, f.name))
            for f in dataclasses.fields(obj)
            if not f.metadata.get("internal")
        }
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, 4)
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    return str(obj)


@dataclass
class SourceInfo:
    format: str = ""
    pages: int = 1
    bytes: int = 0
    sha256: str = ""
    text_sha256: str = ""
    extraction_method: str = ""
    ocr_used: bool = False


@dataclass
class Link:
    url: str
    type: str = "other"
    label: str = ""


@dataclass
class Phone:
    e164: str
    raw: str
    region: str = ""


@dataclass
class Contact:
    name: str = ""
    name_confidence: float = 0.0
    email: str = ""
    emails: List[str] = field(default_factory=list)
    phone: str = ""
    phones: List[Phone] = field(default_factory=list)
    location: str = ""
    links: List[Link] = field(default_factory=list)


@dataclass
class ExperienceEntry:
    title: str = ""
    company: str = ""
    location: str = ""
    start: Optional[str] = None            # 'YYYY-MM'
    end: Optional[str] = None              # 'YYYY-MM' or None when current
    is_current: bool = False
    duration_months: int = 0
    bullets: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    confidence: float = 0.0
    employment_type: str = "full_time"      # full_time|internship|freelance|contract|part_time
    date_text: str = ""
    date_precision: str = "month"
    # internal: month indices (not serialised)
    start_ym: Optional[int] = field(default=None, metadata={"internal": True})
    end_ym: Optional[int] = field(default=None, metadata={"internal": True})
    line_range: Optional[tuple] = field(default=None, metadata={"internal": True})
    issues: List[str] = field(default_factory=list, metadata={"internal": True})
    skill_idx: Any = field(default_factory=set, metadata={"internal": True})


@dataclass
class EducationEntry:
    degree_level: str = "other"
    degree_raw: str = ""
    field: str = ""
    institution: str = ""
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    gpa: Optional[float] = None
    gpa_scale: Optional[float] = None
    confidence: float = 0.0


@dataclass
class Project:
    name: str = ""
    description: str = ""
    skills: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)


@dataclass
class Certification:
    name: str = ""
    issuer: str = ""
    year: Optional[int] = None


@dataclass
class LanguageSkill:
    name: str = ""
    proficiency: str = ""


@dataclass
class ParsedResume:
    schema_version: str = SCHEMA_VERSION
    source: SourceInfo = field(default_factory=SourceInfo)
    contact: Contact = field(default_factory=Contact)
    summary: str = ""
    skills: List[Skill] = field(default_factory=list)
    soft_skills: List[Skill] = field(default_factory=list)
    experience: List[ExperienceEntry] = field(default_factory=list)
    education: List[EducationEntry] = field(default_factory=list)
    projects: List[Project] = field(default_factory=list)
    certifications: List[Certification] = field(default_factory=list)
    languages: List[LanguageSkill] = field(default_factory=list)
    total_experience_months: int = 0
    total_experience_years: float = 0.0
    seniority_estimate: str = "fresher"
    field_confidence: Dict[str, float] = field(default_factory=dict)
    overall_confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)
    sections_detected: List[Dict[str, Any]] = field(default_factory=list)
    redacted_fields: List[str] = field(default_factory=list)
    integrity: Any = None
    probe_plan: Any = None
    # the cleaned text (hidden text and protected attributes removed). Not part of to_dict().
    text: str = field(default="", repr=False, metadata={"internal": True})

    def to_dict(self, include_text: bool = False) -> Dict[str, Any]:
        d = to_jsonable(self)
        if include_text:
            d["text"] = self.text
        return d

    @property
    def redacted_text(self) -> str:
        """``text`` with e-mails, phone numbers and URLs masked (safe for logs)."""
        from .privacy import redact_pii

        return redact_pii(self.text)
