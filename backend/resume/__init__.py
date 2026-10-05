"""Offline, deterministic, no-LLM resume understanding engine.

>>> from resume import parse_resume
>>> result = parse_resume("cv.pdf")          # path or bytes
>>> result.to_dict()                         # JSON serialisable (schema_version "1.0")

Pure Python library: no Django import, no network, no model downloads.
See ``README.md`` for architecture, schema and the integrity flag catalogue.

Privacy: date of birth, age, gender, marital status, nationality, religion, caste,
ethnicity, family details, ID numbers and photos are dropped before analysis and
never appear in the output (``redacted_fields`` lists which categories were seen).
"""
from .errors import (  # noqa: F401
    EmptyResume, EncryptedFile, FileTooLarge, MaliciousFile, ParseTimeout, ResumeParseError, TooManyPages,
    UnsupportedFormat,
)
from .integrity import Fingerprint, Flag, Integrity, similarity  # noqa: F401
from .models import SCHEMA_VERSION, ParsedResume  # noqa: F401
from .pipeline import parse_resume, parse_resume_isolated  # noqa: F401
from .plan import ProbePlan, ProbeTopic, VerificationClaim, build_probe_plan  # noqa: F401
from .privacy import redact_pii, sanitize_for_prompt  # noqa: F401
from .validate import Limits  # noqa: F401

__all__ = [
    "SCHEMA_VERSION", "ParsedResume", "parse_resume", "parse_resume_isolated", "build_probe_plan", "ProbePlan",
    "ProbeTopic", "VerificationClaim", "Integrity", "Flag", "Fingerprint", "similarity", "redact_pii",
    "sanitize_for_prompt", "Limits", "ResumeParseError", "UnsupportedFormat", "MaliciousFile", "ParseTimeout",
    "EncryptedFile", "FileTooLarge", "TooManyPages", "EmptyResume",
]
