"""Section segmentation: heading lexicon + formatting cues.

A line is a heading when its normalised text equals (or fuzzy-matches at
>= 90) a known heading synonym, is short (<= 5 words), is not a bullet and
does not read like a sentence.  Formatting (UPPERCASE, bold, larger font,
trailing colon, rule underneath, DOCX heading style) raises confidence and is
*required* for fuzzy matches.  ``Experience with Python...`` is therefore never
a heading (too long, not a whole-line match, ends like a sentence).
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .extract import Line

LEXICON: Dict[str, List[str]] = {
    "summary": [
        "summary", "professional summary", "career summary", "executive summary", "profile",
        "professional profile", "personal profile", "career profile", "objective", "career objective",
        "professional objective", "job objective", "about me", "about", "overview", "personal statement",
        "summary of qualifications", "qualifications summary", "summary of skills and experience",
        "profile summary", "introduction", "bio", "highlights", "career highlights", "professional overview",
    ],
    "experience": [
        "experience", "work experience", "professional experience", "employment history", "employment",
        "career history", "work history", "relevant experience", "internships", "internship",
        "internship experience", "industry experience", "professional background", "work background",
        "experience summary", "employment experience", "job experience", "career experience",
        "professional work experience", "working experience", "work and internship experience",
        "industrial experience", "internships and training", "experience and internships",
        "positions held", "employment record", "related experience", "research experience",
        "teaching experience", "relevant work experience", "full time experience",
    ],
    "education": [
        "education", "academic background", "academic qualifications", "educational qualifications",
        "education and training", "educational background", "academics", "academic profile",
        "qualifications", "education qualification", "academic record", "scholastic record",
        "education details", "educational details", "academic details", "academic credentials",
        "education & certifications",
    ],
    "skills": [
        "skills", "technical skills", "core competencies", "key skills", "technologies", "tools",
        "skills and tools", "areas of expertise", "expertise", "technical expertise", "core skills",
        "skills summary", "skill set", "technical proficiencies", "technical proficiency", "competencies",
        "tech stack", "technical competencies", "skills and technologies", "technologies and tools",
        "tools and technologies", "professional skills", "it skills", "computer skills", "software skills",
        "technical knowledge", "areas of expertise and skills", "key competencies", "skills and abilities",
        "skills & expertise", "technical summary", "programming skills", "skills profile",
    ],
    "projects": [
        "projects", "personal projects", "academic projects", "selected projects", "key projects",
        "project experience", "notable projects", "side projects", "project work", "major projects",
        "technical projects", "relevant projects", "open source projects", "open source contributions",
        "projects and research", "project", "college projects", "mini projects", "portfolio",
        "software projects", "projects undertaken",
    ],
    "certifications": [
        "certifications", "certificates", "licenses", "licenses and certifications", "licences",
        "certifications and licenses", "courses", "training", "training and certifications",
        "courses and certifications", "professional certifications", "certification", "online courses",
        "professional development", "training programs", "trainings", "courses completed",
        "licenses & certifications", "certifications & training", "workshops", "moocs",
    ],
    "awards": [
        "awards", "honors", "honours", "awards and honors", "achievements", "accomplishments",
        "awards and achievements", "honors and awards", "recognition", "scholarships",
        "awards & achievements", "achievements and awards", "academic achievements", "key achievements",
    ],
    "publications": [
        "publications", "research publications", "papers", "patents", "publications and patents",
        "research papers", "research", "selected publications", "conference papers",
    ],
    "languages": [
        "languages", "language skills", "spoken languages", "language proficiency", "languages known",
        "linguistic skills", "foreign languages", "language",
    ],
    "interests": [
        "interests", "hobbies", "hobbies and interests", "personal interests", "activities",
        "extracurricular activities", "extra curricular activities", "extracurriculars", "co-curricular activities",
        "interests and hobbies", "hobbies & interests", "extracurricular",
    ],
    "volunteering": [
        "volunteering", "volunteer experience", "volunteer work", "community service", "leadership",
        "positions of responsibility", "leadership experience", "community involvement", "volunteer",
        "leadership and activities", "social work", "responsibilities", "leadership & activities",
    ],
    "references": ["references", "referees", "reference", "professional references"],
    "declaration": ["declaration", "self declaration", "personal declaration"],
    "personal": [
        "personal details", "personal information", "personal data", "personal particulars",
        "personal profile details", "bio data", "biodata", "personal",
    ],
    "contact": ["contact", "contact information", "contact details", "contact me", "get in touch", "links", "social links", "online presence"],
}

# Sections whose inline "Label: content" form starts a section (e.g. "Skills: Python, Java").
INLINE_SECTIONS = {"skills", "summary"}

_SYNONYM: Dict[str, str] = {}
for _sec, _syns in LEXICON.items():
    for _s in _syns:
        _SYNONYM[_s] = _sec
_FUZZY_CHOICES = [s for s in _SYNONYM if len(s) >= 7]


def _norm_heading(text: str) -> str:
    t = text.lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9\s/-]", " ", t)
    t = t.replace("/", " ").replace("-", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


@dataclass
class Section:
    name: str
    heading: str
    start: int                 # index of the heading line (0 for the header block)
    end: int                   # exclusive
    confidence: float = 1.0
    inline: bool = False

    @property
    def content_start(self) -> int:
        return self.start if self.name == "header" else self.start + (0 if self.inline else 1)

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "heading": self.heading, "start_line": self.start, "end_line": self.end}


def _fuzzy(norm: str) -> Optional[str]:
    if len(norm) < 7:
        return None
    try:
        from rapidfuzz import fuzz, process
    except ImportError:  # pragma: no cover
        return None
    hit = process.extractOne(norm, _FUZZY_CHOICES, scorer=fuzz.ratio, score_cutoff=90)
    return _SYNONYM[hit[0]] if hit else None


def classify_heading_text(text: str) -> Tuple[Optional[str], bool]:
    """Return ``(section, exact)`` for a candidate heading string."""
    norm = _norm_heading(text)
    if not norm or len(norm.split()) > 6:
        return None, False
    sec = _SYNONYM.get(norm)
    if sec:
        return sec, True
    sec = _fuzzy(norm)
    return (sec, False) if sec else (None, False)


def _body_font(lines: List[Line]) -> float:
    sizes = [ln.font_size for ln in lines if ln.font_size > 0 for _ in range(min(len(ln.text), 200) // 10 + 1)]
    return statistics.median(sizes) if sizes else 0.0


def _is_sentence_like(text: str) -> bool:
    return bool(re.search(r"[.!?]$", text)) and len(text.split()) > 2


def detect_headings(lines: List[Line]) -> List[Tuple[int, str, float, bool, str]]:
    """Return ``[(line_idx, section, confidence, inline, heading_text)]``."""
    body = _body_font(lines)
    found: List[Tuple[int, str, float, bool, str]] = []
    for i, ln in enumerate(lines):
        if ln.is_bullet:
            continue
        raw = ln.text.strip()
        if not raw or len(raw) > 60:
            continue
        # strip decorative edges:  "— EXPERIENCE —", "## Experience", "Experience ───"
        cleaned = re.sub(r"^[\s#*_=\-–—~•·|>\[\(]+|[\s#*_=\-–—~•·|<\]\)]+$", "", raw)
        colon = cleaned.endswith(":")
        cleaned = cleaned.rstrip(":").strip()
        if not cleaned:
            continue
        words = cleaned.split()
        fmt = 0.0
        if ln.is_upper:
            fmt += 0.3
        if ln.is_bold:
            fmt += 0.25
        if body and ln.font_size >= body * 1.08:
            fmt += 0.25
        if colon:
            fmt += 0.15
        if ln.rule_below:
            fmt += 0.25
        if ln.heading_style:
            fmt += 0.4
        if len(words) <= 6 and "\t" not in cleaned:
            sec, exact = classify_heading_text(cleaned)
            if sec and not _is_sentence_like(cleaned):
                if exact and len(words) <= 5:
                    found.append((i, sec, min(1.0, 0.7 + fmt * 0.5), False, cleaned))
                    continue
                if not exact and fmt >= 0.25:
                    found.append((i, sec, min(1.0, 0.5 + fmt * 0.4), False, cleaned))
                    continue
    return found


_INLINE_RE = re.compile(r"^([A-Za-z &/]{3,32}?)\s*[:\-–]\s+(\S.*)$")


_META_INLINE = {"tech stack", "technologies", "technology", "tools", "tools used", "stack", "environment", "tech"}


def split_inline_labels(lines: List[Line]) -> List[Line]:
    """``Skills: Python, Java`` -> heading line ``Skills:`` + content line (skills/summary only).

    Meta labels such as ``Tech Stack:`` / ``Environment:`` are *not* split while inside an experience or
    projects section, where they annotate a single entry.
    """
    import copy

    heads = {i: sec for i, sec, *_ in detect_headings(lines)}
    out: List[Line] = []
    current = "header"
    for i, ln in enumerate(lines):
        if i in heads:
            current = heads[i]
        m = None if ln.is_bullet else _INLINE_RE.match(ln.text.strip())
        if m and len(m.group(1).split()) <= 3:
            sec, exact = classify_heading_text(m.group(1))
            label = _norm_heading(m.group(1))
            meta_inside = label in _META_INLINE and current in ("experience", "projects")
            if sec in INLINE_SECTIONS and exact and not meta_inside:
                head = copy.copy(ln)
                head.text = m.group(1).strip() + ":"
                body = copy.copy(ln)
                body.text = m.group(2).strip()
                body.is_bold = False
                body.blank_before = False
                out.extend([head, body])
                current = sec
                continue
        out.append(ln)
    return out


def segment(lines: List[Line]) -> List[Section]:
    """Split ``lines`` into ordered sections; the leading block is ``header``."""
    heads = detect_headings(lines)
    # Drop duplicate heads on consecutive lines (e.g. "Skills" + "Technical Skills" underline)
    sections: List[Section] = []
    first = heads[0][0] if heads else len(lines)
    if first > 0 or not heads:
        sections.append(Section("header", "", 0, first, 1.0))
    for k, (idx, sec, conf, inline, text) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        sections.append(Section(sec, text, idx, end, conf, inline))
    # An all-caps/inline heading found inside the first lines is fine; but a "header" with no lines is dropped.
    return [s for s in sections if not (s.name == "header" and s.end <= s.start)]


def section_of_line(sections: List[Section], idx: int) -> str:
    for s in sections:
        if s.start <= idx < s.end:
            return s.name
    return "header"


def lines_of(lines: List[Line], sections: List[Section], *names: str) -> List[Tuple[int, Line]]:
    """All ``(index, line)`` belonging to sections called ``names`` (heading line excluded)."""
    out: List[Tuple[int, Line]] = []
    for s in sections:
        if s.name in names:
            for i in range(s.content_start, s.end):
                out.append((i, lines[i]))
    return out
