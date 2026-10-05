"""Exception hierarchy for the resume engine.

All exceptions subclass :class:`ResumeParseError` which itself subclasses
``ValueError`` so legacy callers that only catch ``ValueError`` keep working.
Every exception takes a single message argument so they pickle cleanly across
process boundaries (see :func:`resume.parse_resume_isolated`).

Suggested HTTP mapping for a Django integration layer:

=================  ====  =====================================================
Exception          HTTP  Meaning
=================  ====  =====================================================
UnsupportedFormat  415   not PDF/DOCX/TXT (e.g. legacy .doc, images, binary)
FileTooLarge       413   over the byte limit
TooManyPages       413   PDF over the hard page limit
EncryptedFile      422   password protected PDF
MaliciousFile      422   zip bomb / active content (Launch, JS+OpenAction)
EmptyResume        422   readable but no text (e.g. scanned PDF without OCR)
ParseTimeout       504   time budget exhausted
ResumeParseError   422   anything else
=================  ====  =====================================================
"""
from __future__ import annotations


class ResumeParseError(ValueError):
    """Base class for all hard failures of the engine."""


class UnsupportedFormat(ResumeParseError):
    """The file is not a supported resume format."""


class FileTooLarge(ResumeParseError):
    """The file exceeds the configured byte limit."""


class TooManyPages(ResumeParseError):
    """The PDF exceeds the hard page limit."""


class EncryptedFile(ResumeParseError):
    """The document is password protected."""


class MaliciousFile(ResumeParseError):
    """The file looks hostile (zip bomb, launch actions, JS auto-run)."""


class EmptyResume(ResumeParseError):
    """The file was readable but contained no usable text."""


class ParseTimeout(ResumeParseError):
    """The cooperative time budget (or the isolated hard timeout) expired."""
