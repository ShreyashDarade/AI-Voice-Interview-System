import io
import zipfile

import pymupdf
import pytest

from resume import (
    EmptyResume, EncryptedFile, FileTooLarge, Limits, MaliciousFile, ParseTimeout, ResumeParseError, TooManyPages,
    UnsupportedFormat, parse_resume,
)
from resume import validate as V
from resume.extract import extract_txt
from resume.tests.helpers import PdfBuilder, docx_bytes, raw_pdf, simple_pdf, zip_bytes

GOOD_LINES = ["Jane Doe", "jane@doe.dev | +1 415 555 0132", ("EXPERIENCE", 12, True),
              "Engineer, Acme Inc. Jan 2020 - Dec 2021", "• Built Python services"]


def good_pdf():
    return simple_pdf(GOOD_LINES)


# ------------------------------------------------------------------ type sniffing
def test_pdf_detected_by_magic_not_extension():
    r = parse_resume(good_pdf(), "resume.txt")
    assert r.source.format == "pdf"
    codes = {f.code for f in r.integrity.flags}
    assert "extension_mismatch" in codes


def test_text_with_pdf_extension_is_text():
    r = parse_resume(b"Jane Doe\njane@doe.dev\nEXPERIENCE\nEngineer at Acme 2019 - 2020\n", "cv.pdf")
    assert r.source.format == "txt"
    assert any(f.code == "extension_mismatch" for f in r.integrity.flags)


def test_legacy_doc_gives_helpful_error():
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600
    with pytest.raises(UnsupportedFormat) as ei:
        parse_resume(ole, "resume.doc")
    assert "DOCX" in str(ei.value) or "docx" in str(ei.value).lower()


@pytest.mark.parametrize("blob,desc", [
    (b"{\\rtf1\\ansi Hello}", "rtf"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "png"),
    (b"\xff\xd8\xff\xe0" + b"\x00" * 100, "jpeg"),
    (bytes(range(256)) * 20, "binary garbage"),
    (b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200, "exe"),
])
def test_unsupported_and_binary_inputs_rejected(blob, desc):
    with pytest.raises(UnsupportedFormat):
        parse_resume(blob, "x.pdf")


def test_other_office_and_plain_zip_rejected():
    xlsx = zip_bytes({"[Content_Types].xml": "<x/>", "xl/workbook.xml": "<x/>"})
    with pytest.raises(UnsupportedFormat):
        parse_resume(xlsx, "x.docx")
    with pytest.raises(UnsupportedFormat):
        parse_resume(zip_bytes({"a.txt": "hello"}), "x.zip")


def test_empty_and_whitespace_files():
    for blob in (b"", b"   \n\t  ", b"\x00\x00\x00"):
        with pytest.raises(ResumeParseError):
            parse_resume(blob, "x.txt")


def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_resume(tmp_path / "nope.pdf")


def test_path_input_works(tmp_path):
    p = tmp_path / "cv.pdf"
    p.write_bytes(good_pdf())
    r = parse_resume(p)
    assert r.contact.email == "jane@doe.dev" and r.source.bytes == p.stat().st_size


# ------------------------------------------------------------------ text encodings
@pytest.mark.parametrize("raw,enc,expected", [
    ("Zoë Müller – café".encode("utf-8-sig"), "utf-8-sig", "Zoë Müller – café"),
    ("Zoë Müller – café".encode("utf-16"), "utf-16", "Zoë Müller – café"),
    ("Zoë Müller – café".encode("utf-16-le"), "utf-16-le", "Zoë Müller – café"),
    ("Zoë Müller – café".encode("utf-16-be"), "utf-16-be", "Zoë Müller – café"),
    ("Zoë Müller – café".encode("utf-8"), "utf-8", "Zoë Müller – café"),
    ("José “Pepe” García – résumé".encode("cp1252"), "cp1252", "José “Pepe” García – résumé"),
    (b"Caf\xe9 M\xfcller", "cp1252", "Café Müller"),
    (b"Name \x81\x8d\x8f", "latin-1", "Name \x81\x8d\x8f"),
])
def test_decode_text_sniffing(raw, enc, expected):
    text, used = V.decode_text(raw)
    assert used == enc and text == expected


def test_txt_resume_in_cp1252_and_utf16_parse_the_same():
    body = "Zoë Müller\nzoe@mueller.de\nSKILLS\nPython, Docker\n"
    a = parse_resume(body.encode("cp1252"), "a.txt")
    b = parse_resume(body.encode("utf-16"), "b.txt")
    assert a.contact.name == b.contact.name == "Zoë Müller"
    assert {s.name for s in a.skills} == {s.name for s in b.skills}


def test_txt_normalisation_of_bullets_quotes_ligatures_zero_width():
    raw = "Jane Doe\n​EXPERIENCE\n• one\n▪ two\n● three\n- four\n– five\n* six\n➢ seven\n◦ eight\n✓ nine\n ten\nﬁnal ﬂow “quoted” it’s\n"
    doc = extract_txt(raw.encode("utf-8"))
    texts = [l.text for l in doc.lines]
    assert texts[0] == "Jane Doe" and texts[1] == "EXPERIENCE"
    bullets = [l for l in doc.lines if l.is_bullet]
    assert [b.text for b in bullets] == ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
    assert texts[-1] == 'final flow "quoted" it\'s'


# ------------------------------------------------------------------ size / pages / encryption
def test_oversize_rejected_before_parsing():
    with pytest.raises(FileTooLarge):
        parse_resume(b"a" * 2001, "x.txt", limits=Limits(max_bytes=2000))


def test_oversize_file_on_disk(tmp_path):
    p = tmp_path / "big.txt"
    p.write_bytes(b"hello " * 1000)
    with pytest.raises(FileTooLarge):
        parse_resume(p, limits=Limits(max_bytes=100))


def _many_pages(n):
    b = PdfBuilder()
    for i in range(n):
        if i:
            b.new_page()
        b.flow(50, 60, [f"Page body {i} " + "lorem ipsum dolor sit amet " * 8, "Python developer " * 5] * 6)
    return b.bytes()


def test_page_limits_truncate_then_fail():
    pdf = _many_pages(25)
    r = parse_resume(pdf, limits=Limits(max_pages=20, hard_max_pages=200))
    assert "truncated_pages" in r.warnings
    assert any(f.code == "truncated_pages" for f in r.integrity.flags)
    assert "Page body 19" in r.text and "Page body 20" not in r.text
    with pytest.raises(TooManyPages):
        parse_resume(pdf, limits=Limits(max_pages=5, hard_max_pages=24))


def test_encrypted_pdf_rejected_clearly():
    b = PdfBuilder()
    b.flow(50, 60, GOOD_LINES)
    enc = b.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    with pytest.raises(EncryptedFile) as ei:
        parse_resume(enc, "enc.pdf")
    assert "password" in str(ei.value).lower()


def test_corrupt_pdf_is_a_clean_error():
    with pytest.raises(ResumeParseError):
        parse_resume(b"%PDF-1.7\n" + b"garbage" * 50, "x.pdf")


# ------------------------------------------------------------------ DOCX hostility
def test_zip_bomb_detected_by_ratio():
    bomb = zip_bytes({"word/document.xml": b"\x00" * (60 * 1024 * 1024)})
    assert len(bomb) < 2 * 1024 * 1024
    with pytest.raises(MaliciousFile):
        parse_resume(bomb, "bomb.docx")


def test_zip_with_too_many_members():
    many = zip_bytes({"word/document.xml": "<x/>", **{f"f{i}.txt": "a" for i in range(1500)}})
    with pytest.raises(MaliciousFile):
        parse_resume(many, "x.docx")


def test_zip_path_traversal_member_rejected():
    z = zip_bytes({"word/document.xml": "<x/>", "../../evil.sh": "rm -rf /"})
    with pytest.raises(MaliciousFile):
        parse_resume(z, "x.docx")


def test_bounded_reader_ignores_declared_sizes():
    z = zipfile.ZipFile(io.BytesIO(zip_bytes({"word/document.xml": b"A" * 5000})))
    with pytest.raises(MaliciousFile):
        V.safe_read_member(z, "word/document.xml", Limits(max_zip_member_bytes=1000))
    assert len(V.safe_read_member(z, "word/document.xml", Limits())) == 5000


def test_docx_with_entity_declaration_rejected_no_expansion():
    xml = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
           b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&lol2;</w:t></w:r></w:p></w:body></w:document>')
    with pytest.raises(MaliciousFile):
        parse_resume(zip_bytes({"word/document.xml": xml, "[Content_Types].xml": "<x/>"}), "x.docx")


def test_docx_external_entity_not_resolved():
    xml = (b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
           b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p></w:body></w:document>')
    with pytest.raises(MaliciousFile):
        parse_resume(zip_bytes({"word/document.xml": xml}), "x.docx")


# ------------------------------------------------------------------ PDF active content
def test_pdf_javascript_with_open_action_is_rejected():
    with pytest.raises(MaliciousFile):
        parse_resume(raw_pdf(b"/OpenAction << /S /JavaScript /JS (app.alert(1)) >>"), "x.pdf")


def test_pdf_launch_action_is_rejected():
    with pytest.raises(MaliciousFile):
        parse_resume(raw_pdf(b"/OpenAction << /S /Launch /F (cmd.exe) >>"), "x.pdf")


def test_pdf_javascript_without_autorun_is_flagged_not_fatal():
    r = parse_resume(raw_pdf(b"/Names << /JavaScript << /Names [(a) << /S /JavaScript /JS (x) >>] >> >>"), "x.pdf")
    flag = r.integrity.get("pdf_active_content")
    assert flag and flag.severity == "medium" and "JavaScript" in flag.details["tokens"]
    assert r.contact.email == "jane@doe.dev"           # still parsed


def test_pdf_embedded_file_is_flagged():
    b = PdfBuilder()
    b.flow(50, 60, GOOD_LINES)
    b.doc.embfile_add("payload.exe", b"MZ....")
    r = parse_resume(b.bytes(), "x.pdf")
    flag = r.integrity.get("pdf_active_content")
    assert flag and "EmbeddedFile" in flag.details["tokens"]


def test_clean_pdf_has_no_active_content_flag():
    assert parse_resume(good_pdf()).integrity.get("pdf_active_content") is None


def test_binary_stream_noise_does_not_trigger_false_malicious_detection():
    # random bytes inside a stream may contain "/JS" or "/Launch" sequences; only real dictionary keys count
    pdf = raw_pdf()
    noisy = pdf.replace(b"endstream", b"/Launch /JS /OpenAction\nendstream")
    r = parse_resume(noisy, "x.pdf")
    assert r.integrity.get("pdf_active_content") is None


# ------------------------------------------------------------------ time budget
def test_cooperative_timeout():
    with pytest.raises(ParseTimeout):
        parse_resume(good_pdf(), time_budget_s=-1)


def test_deadline_object():
    from resume.util import Deadline

    t = [0.0]
    d = Deadline(5, clock=lambda: t[0])
    d.check("a")
    t[0] = 6
    with pytest.raises(ParseTimeout):
        d.check("stage")
    assert Deadline(None).remaining() == float("inf")
