"""Resume ingestion: hardened parse -> model fields. The only place the Django models meet the resume engine."""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from core.models import Resume
from interview.resume_parser import to_legacy
from llm import resume_enrich
from resume import (EmptyResume, EncryptedFile, FileTooLarge, MaliciousFile, ParseTimeout, ResumeParseError,
                    TooManyPages, UnsupportedFormat, similarity)
from resume import parse_resume, parse_resume_isolated
from resume.plan import ProbePlan, ProbeTopic, VerificationClaim

logger = logging.getLogger(__name__)

#: engine error -> (http status, machine code)
ERROR_MAP = {
    UnsupportedFormat: (415, 'unsupported_format'), FileTooLarge: (413, 'file_too_large'),
    MaliciousFile: (422, 'unsafe_file'), EncryptedFile: (422, 'encrypted_file'), TooManyPages: (413, 'too_many_pages'),
    EmptyResume: (422, 'no_text'), ParseTimeout: (504, 'parse_timeout'),
}
NEAR_DUPLICATE = 0.92


def error_for(exc: ResumeParseError) -> tuple[int, str]:
    for cls, v in ERROR_MAP.items():
        if isinstance(exc, cls):
            return v
    return 422, 'unparsable'


def _plan_from_dict(d: dict[str, Any]) -> ProbePlan:
    return ProbePlan(seniority=d.get('seniority', 'fresher'),
                     topics=[ProbeTopic(**t) for t in d.get('topics', [])],
                     verification_claims=[VerificationClaim(**c) for c in d.get('verification_claims', [])],
                     total_experience_months=d.get('total_experience_months', 0),
                     integrity_notes=d.get('integrity_notes', []))


def find_duplicates(resume: Resume, simhash: str, sha: str, owner_id) -> dict[str, list[str]]:
    qs = Resume.objects.exclude(pk=resume.pk)
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    exact = list(qs.filter(content_sha256=sha).values_list('id', flat=True)[:10]) if sha else []
    near = []
    for rid, sh in qs.exclude(simhash='').order_by('-created_at').values_list('id', 'simhash')[:500]:
        if rid not in exact and similarity({'simhash': simhash}, {'simhash': sh}) >= NEAR_DUPLICATE:
            near.append(rid)
    return {'exact': [str(i) for i in exact], 'near': [str(i) for i in near[:10]]}


def ingest(resume: Resume) -> dict[str, list[str]]:
    """Parse ``resume.file`` and populate the model. Raises ``ResumeParseError`` (caller deletes the row)."""
    path = resume.file.path
    name = resume.original_filename
    if settings.RESUME_ISOLATED_PARSE:
        engine = parse_resume_isolated(path, name, include_text=True)
        text = engine.pop('text', '')
    else:
        parsed = parse_resume(path, name)
        engine, text = parsed.to_dict(), parsed.text
    engine = resume_enrich.enrich(engine, text)                  # no-op unless an LLM provider is configured

    legacy = to_legacy(engine, text)
    plan = engine.get('probe_plan') or {}
    prompt = _plan_from_dict(plan).to_prompt_context() if plan else ''
    integrity = engine.get('integrity') or {}
    fp = integrity.get('fingerprint') or {}

    resume.raw_text = text
    resume.parsed_data = {k: v for k, v in legacy.items() if k != 'raw_text'} | {'probe_prompt': prompt}
    resume.candidate_name = (legacy['name'] or '')[:255]
    resume.email = (legacy['email'] or '')[:254]
    resume.phone = (legacy['phone'] or '')[:50]
    resume.experience_years = min(float(legacy['experience_years'] or 0), 50)
    resume.skills = legacy['skills']
    resume.education = legacy['education']
    resume.work_history = legacy['work_history']
    resume.parse_schema_version = engine.get('schema_version', '')
    resume.parse_confidence = float(engine.get('overall_confidence') or 0)
    resume.integrity = integrity
    resume.probe_plan = plan
    resume.content_sha256 = fp.get('text_sha256', '')
    resume.simhash = fp.get('simhash', '')
    resume.save()
    return find_duplicates(resume, resume.simhash, resume.content_sha256, resume.owner_id)
