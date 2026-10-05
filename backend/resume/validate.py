"""Safe ingestion: type sniffing, size/page limits, zip-bomb and PDF active-content checks.

Policy (documented, deliberate):

* The *real* type is decided from magic bytes, never from the extension.
  A mismatching extension is only reported as an info flag.
* PDF, DOCX and TXT are supported.  Legacy ``.doc`` (OLE), RTF, images, other
  ZIP-based formats and binary garbage raise :class:`UnsupportedFormat`.
* ``max_bytes`` (10 MB) -> :class:`FileTooLarge`.  Pages: <= ``max_pages`` (20)
  parse normally, ``max_pages`` < n <= ``hard_max_pages`` (200) are truncated
  with a ``truncated_pages`` warning, > 200 -> :class:`TooManyPages`.
* Password protected PDFs -> :class:`EncryptedFile`.
* DOCX are ZIP containers: member count, total *actual* uncompressed size and
  per-member compression ratio are capped; violations -> :class:`MaliciousFile`.
  Members are always read through a bounded reader, never trusting the
  declared sizes in the zip header.
* PDF active content is **never executed**.  We scan the raw bytes (outside
  stream bodies) and the object table via PyMuPDF for ``/JavaScript``,
  ``/JS``, ``/Launch``, ``/EmbeddedFile``, ``/OpenAction``, ``/AA``,
  ``/AcroForm`` and ``/XFA``.  ``/Launch`` actions, and JavaScript combined
  with an automatic trigger (``/OpenAction`` or ``/AA``) raise
  :class:`MaliciousFile`.  Every other finding is surfaced as an
  ``pdf_active_content`` integrity flag and parsing continues.
* Nothing is written to disk.
"""
from __future__ import annotations

import io
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .errors import (
    EncryptedFile,
    FileTooLarge,
    MaliciousFile,
    ResumeParseError,
    TooManyPages,
    UnsupportedFormat,
)

BytesLike = Union[bytes, bytearray, memoryview, str, Path]


@dataclass
class Limits:
    max_bytes: int = 10 * 1024 * 1024
    max_pages: int = 20
    hard_max_pages: int = 200
    max_zip_members: int = 1000
    max_zip_total_bytes: int = 64 * 1024 * 1024
    max_zip_member_bytes: int = 32 * 1024 * 1024
    max_zip_ratio: float = 200.0          # per-member uncompressed/compressed
    ratio_floor_bytes: int = 1024 * 1024  # ratio only enforced above this size
    max_text_chars: int = 400_000       # extracted text beyond this is dropped (warning ``text_truncated``)
    max_line_chars: int = 4_000


@dataclass
class Sniffed:
    fmt: str                      # 'pdf' | 'docx' | 'txt'
    data: bytes
    sha256: str
    flags: List[Dict[str, Any]] = field(default_factory=list)   # integrity flag dicts
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- loading

def load_bytes(src: BytesLike, limits: Limits) -> bytes:
    """Read bytes from a path or accept bytes; enforce the size limit *before* reading all."""
    if isinstance(src, (bytes, bytearray, memoryview)):
        data = bytes(src)
        if len(data) > limits.max_bytes:
            raise FileTooLarge(f"file is {len(data)} bytes; limit is {limits.max_bytes}")
        return data
    if isinstance(src, (str, Path)):
        p = Path(src)
        if not p.is_file():
            raise FileNotFoundError(f"Resume file not found: {p}")
        size = p.stat().st_size
        if size > limits.max_bytes:
            raise FileTooLarge(f"file is {size} bytes; limit is {limits.max_bytes}")
        with p.open("rb") as fh:
            data = fh.read(limits.max_bytes + 1)
        if len(data) > limits.max_bytes:
            raise FileTooLarge(f"file exceeds {limits.max_bytes} bytes")
        return data
    raise TypeError("parse_resume expects a path or bytes")


_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def sniff_format(data: bytes, filename: Optional[str] = None) -> Tuple[str, List[Dict[str, Any]]]:
    """Return ``(format, flags)``; raises :class:`UnsupportedFormat`."""
    flags: List[Dict[str, Any]] = []
    if not data or not data.strip(b"\x00 \t\r\n"):
        raise ResumeParseError("file is empty")
    head = data[:2048]
    fmt: Optional[str] = None
    if b"%PDF-" in head[:1024]:
        fmt = "pdf"
    elif data[:4] == b"PK\x03\x04" or data[:4] == b"PK\x05\x06":
        fmt = _classify_zip(data)
    elif data[:8] == _OLE_MAGIC:
        raise UnsupportedFormat(
            "Legacy Word .doc (OLE) files are not supported; please re-save the resume as DOCX or PDF."
        )
    elif data[:5] == b"{\\rtf":
        raise UnsupportedFormat("RTF files are not supported; please re-save the resume as DOCX or PDF.")
    elif data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or data[:4] in (b"GIF8", b"II*\x00", b"MM\x00*"):
        raise UnsupportedFormat("Image files are not supported; please upload a PDF, DOCX or TXT resume.")
    elif data[:4] == b"\x7fELF" or (data[:2] == b"MZ" and b"\x00" in data[:256]):
        raise UnsupportedFormat("Executable content is not a resume.")
    else:
        fmt = "txt"  # validated by decode_text
    ext = Path(filename).suffix.lower().lstrip(".") if filename else ""
    if ext and fmt and ext in {"pdf", "docx", "txt", "doc", "rtf", "md"} and ext != fmt and not (fmt == "txt" and ext == "md"):
        flags.append(
            {
                "code": "extension_mismatch",
                "severity": "info",
                "message": f"File extension '.{ext}' does not match detected content type '{fmt}'.",
                "details": {"extension": ext, "detected": fmt},
            }
        )
    return fmt, flags


def _classify_zip(data: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError, zipfile.LargeZipFile) as exc:
        raise UnsupportedFormat(f"corrupt zip container: {exc}") from exc
    with zf:
        names = set(zf.namelist()[:5000])
    if "word/document.xml" in names:
        return "docx"
    if any(n.startswith("xl/") for n in names) or any(n.startswith("ppt/") for n in names):
        raise UnsupportedFormat("Only DOCX, PDF and TXT resumes are supported (got another Office format).")
    if "content.xml" in names and "mimetype" in names:
        raise UnsupportedFormat("OpenDocument files are not supported; please re-save as DOCX or PDF.")
    raise UnsupportedFormat("ZIP archives are not supported as resumes.")


# --------------------------------------------------------------------------- text

def decode_text(data: bytes) -> Tuple[str, str]:
    """Decode a text resume; returns ``(text, encoding)``. Rejects binary data."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            return data.decode("utf-16", errors="replace"), "utf-16"
    # BOM-less UTF-16: alternating NULs
    if len(data) >= 4 and b"\x00" in data[:200]:
        even_nul = data[0::2].count(0)
        odd_nul = data[1::2].count(0)
        half = len(data) / 2
        if odd_nul > 0.8 * half and even_nul < 0.05 * half:
            return data.decode("utf-16-le", errors="replace"), "utf-16-le"
        if even_nul > 0.8 * half and odd_nul < 0.05 * half:
            return data.decode("utf-16-be", errors="replace"), "utf-16-be"
    if b"\x00" in data:
        raise UnsupportedFormat("File contains NUL bytes and does not look like a text resume.")
    sample = data[:65536]
    ctrl = sum(1 for b in sample if b < 9 or (13 < b < 32 and b != 27) or b == 127)
    if sample and ctrl / len(sample) > 0.02:
        raise UnsupportedFormat("File looks like binary data, not a text resume.")
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


# --------------------------------------------------------------------------- DOCX zip

def check_docx_zip(data: bytes, limits: Limits) -> None:
    """Raise :class:`MaliciousFile` for zip bombs / absurd containers."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError, zipfile.LargeZipFile) as exc:
        raise UnsupportedFormat(f"corrupt DOCX container: {exc}") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > limits.max_zip_members:
            raise MaliciousFile(f"DOCX has {len(infos)} members (limit {limits.max_zip_members}).")
        total = 0
        for zi in infos:
            if zi.filename.startswith("/") or ".." in Path(zi.filename).parts:
                raise MaliciousFile(f"DOCX member with suspicious path: {zi.filename[:80]!r}")
            total += zi.file_size
            if zi.file_size > limits.max_zip_member_bytes:
                raise MaliciousFile(f"DOCX member '{zi.filename[:60]}' is too large when uncompressed.")
            if zi.file_size > limits.ratio_floor_bytes:
                ratio = zi.file_size / max(zi.compress_size, 1)
                if ratio > limits.max_zip_ratio:
                    raise MaliciousFile(
                        f"DOCX member '{zi.filename[:60]}' has a compression ratio of {ratio:.0f}:1 (zip bomb?)."
                    )
        if total > limits.max_zip_total_bytes:
            raise MaliciousFile("DOCX total uncompressed size exceeds the limit (zip bomb?).")


def safe_read_member(zf: zipfile.ZipFile, name: str, limits: Limits) -> bytes:
    """Read a zip member with a hard cap on *actual* decompressed bytes."""
    cap = limits.max_zip_member_bytes
    out = bytearray()
    try:
        with zf.open(name) as fh:
            while True:
                chunk = fh.read(1 << 16)
                if not chunk:
                    break
                out += chunk
                if len(out) > cap:
                    raise MaliciousFile(f"DOCX member '{name[:60]}' exceeds the decompressed size cap.")
    except KeyError:
        raise
    except MaliciousFile:
        raise
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError, OSError, zipfile.LargeZipFile) as exc:
        raise ResumeParseError(f"corrupt DOCX member '{name[:60]}': {str(exc)[:80]}") from exc
    return bytes(out)


# --------------------------------------------------------------------------- PDF scan

_STREAM_RE = re.compile(rb"stream\r?\n.*?endstream", re.S)
_NAME_PATTERNS = {
    "JavaScript": re.compile(rb"/JavaScript(?![A-Za-z0-9#])"),
    "JS": re.compile(rb"/JS(?![A-Za-z0-9#])"),
    "Launch": re.compile(rb"/Launch(?![A-Za-z0-9#])"),
    "EmbeddedFile": re.compile(rb"/EmbeddedFiles?(?![A-Za-z0-9#])"),
    "OpenAction": re.compile(rb"/OpenAction(?![A-Za-z0-9#])"),
    "AA": re.compile(rb"/AA(?![A-Za-z0-9#])"),
    "AcroForm": re.compile(rb"/AcroForm(?![A-Za-z0-9#])"),
    "XFA": re.compile(rb"/XFA(?![A-Za-z0-9#])"),
    "RichMedia": re.compile(rb"/RichMedia(?![A-Za-z0-9#])"),
    "SubmitForm": re.compile(rb"/SubmitForm(?![A-Za-z0-9#])"),
}


def scan_pdf_raw(data: bytes) -> Dict[str, int]:
    """Count active-content name tokens in the *non-stream* part of the file."""
    stripped = _STREAM_RE.sub(b"", data)
    return {k: len(p.findall(stripped)) for k, p in _NAME_PATTERNS.items()}


def scan_pdf_objects(doc: Any, max_objects: int = 60000) -> Dict[str, int]:
    """Count the same tokens by walking the parsed object table (sees object streams)."""
    counts = {k: 0 for k in _NAME_PATTERNS}
    n = min(doc.xref_length(), max_objects)
    for xref in range(1, n):
        try:
            obj = doc.xref_object(xref, compressed=True)
        except Exception:
            continue
        if "/" not in obj:
            continue
        for key, pat in _NAME_PATTERNS.items():
            if pat.search(obj.encode("latin-1", "ignore")):
                counts[key] += 1
    try:
        cat = doc.pdf_catalog()
        cobj = doc.xref_object(cat, compressed=True)
        for key, pat in _NAME_PATTERNS.items():
            if pat.search(cobj.encode("latin-1", "ignore")):
                counts[key] = max(counts[key], 1)
    except Exception:
        pass
    return counts


def evaluate_pdf_active_content(raw: Dict[str, int], objs: Dict[str, int]) -> Tuple[Dict[str, int], Optional[str], List[Dict[str, Any]]]:
    """Combine scans; returns ``(merged_counts, fatal_reason|None, flags)``."""
    merged = {k: max(raw.get(k, 0), objs.get(k, 0)) for k in _NAME_PATTERNS}
    has_js = merged["JavaScript"] > 0 or merged["JS"] > 0
    auto = merged["OpenAction"] > 0 or merged["AA"] > 0
    fatal: Optional[str] = None
    if merged["Launch"] > 0:
        fatal = "PDF contains a /Launch action (can start external programs)."
    elif has_js and auto:
        fatal = "PDF contains JavaScript wired to an automatic trigger (/OpenAction or /AA)."
    present = {k: v for k, v in merged.items() if v}
    flags: List[Dict[str, Any]] = []
    interesting = {k: v for k, v in present.items() if k not in ("AcroForm",)}
    if interesting or present.get("AcroForm"):
        if merged["Launch"] or (has_js and auto):
            sev = "high"
        elif has_js or merged["EmbeddedFile"] or merged["RichMedia"] or merged["SubmitForm"]:
            sev = "medium"
        elif merged["XFA"] or merged["OpenAction"] or merged["AA"]:
            sev = "low"
        else:
            sev = "info"
        flags.append(
            {
                "code": "pdf_active_content",
                "severity": sev,
                "message": "PDF contains active or embedded content (not executed): " + ", ".join(sorted(present)) + ".",
                "details": {"tokens": present},
            }
        )
    return merged, fatal, flags


# --------------------------------------------------------------------------- entry point

def sniff_and_validate(src: BytesLike, filename: Optional[str], limits: Limits) -> Sniffed:
    import hashlib

    if filename is None and isinstance(src, (str, Path)):
        filename = Path(src).name
    data = load_bytes(src, limits)
    fmt, flags = sniff_format(data, filename)
    sha = hashlib.sha256(data).hexdigest()
    out = Sniffed(fmt=fmt, data=data, sha256=sha, flags=flags)
    if fmt == "docx":
        check_docx_zip(data, limits)
    return out
