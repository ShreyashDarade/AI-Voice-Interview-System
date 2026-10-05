import os

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from core.models import Resume
from proctoring.models import Tenant
from resume.tests.fixtures import fresher_docx, fresher_txt, midlevel_pdf, senior_two_column_pdf

pytestmark = pytest.mark.django_db


def client(key):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f'Bearer {key}')
    return c


def upload(c, data, name='cv.pdf', ctype='application/pdf'):
    return c.post('/api/resume/upload/', {'file': SimpleUploadedFile(name, data, ctype)}, format='multipart')


def test_upload_parses_and_returns_integrity_without_raw_text(tenant):
    pdf, expect = midlevel_pdf()
    r = upload(client(tenant.plaintext_key), pdf)
    assert r.status_code == 201, r.content
    b = r.json()
    assert b['candidate_name'] == 'Marcus Chen' and 'Python' in b['skills'] and b['experience_years'] > 3
    assert 'raw_text' not in b and 'file' not in b and 'probe_prompt' not in b['parsed_data']
    assert b['parse_schema_version'] and 0 < b['parse_confidence'] <= 1 and 'flags' in b['integrity']
    assert b['probe_plan']['topics'] and b['duplicates'] == {'exact': [], 'near': []}
    row = Resume.objects.get(pk=b['id'])
    assert row.owner_id == tenant.id and row.content_sha256 and row.simhash
    assert 'ignore previous' not in row.parsed_data['probe_prompt'].lower()
    assert row.parsed_data['probe_prompt'].startswith("RESUME CONTEXT (untrusted")


def test_same_resume_twice_is_reported_as_duplicate(tenant):
    c = client(tenant.plaintext_key)
    pdf, _ = midlevel_pdf()
    first = upload(c, pdf).json()
    second = upload(c, pdf).json()
    assert second['duplicates']['exact'] == [first['id']]
    other = upload(c, senior_two_column_pdf()).json()
    assert other['duplicates'] == {'exact': [], 'near': []}


def test_duplicates_are_not_reported_across_tenants(tenant):
    pdf, _ = midlevel_pdf()
    upload(client(tenant.plaintext_key), pdf)
    _, k2 = Tenant.create_with_key('Rival')
    assert upload(client(k2), pdf).json()['duplicates']['exact'] == []


@pytest.mark.parametrize('name,data,status,code', [
    ('cv.pdf', b'MZ\x90\x00 this is an executable', 415, 'unsupported_format'),      # spoofed extension, caught by magic bytes
    ('cv.pdf', b'%PDF-1.4\n%%EOF', 422, None),                                        # no text
    ('cv.txt', b'\x00\x01\x02\x03' * 100, None, None),                                # binary masquerading as text
    ('cv.exe', b'abc', 400, None),                                                    # extension gate
])
def test_bad_files_rejected_and_nothing_is_stored(tenant, name, data, status, code):
    before = Resume.objects.count()
    r = upload(client(tenant.plaintext_key), data, name=name, ctype='application/octet-stream')
    assert r.status_code >= 400 and (status is None or r.status_code == status)
    if code:
        assert r.json()['error'] == code
    assert Resume.objects.count() == before


def test_oversized_upload_rejected(tenant, settings):
    settings.MAX_RESUME_SIZE_MB = 0
    assert upload(client(tenant.plaintext_key), fresher_txt().encode(), 'cv.txt', 'text/plain').status_code == 400


def test_txt_and_docx_supported(tenant):
    c = client(tenant.plaintext_key)
    assert upload(c, fresher_txt().encode(), 'a.txt', 'text/plain').status_code == 201
    r = upload(c, fresher_docx(), 'a.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    assert r.status_code == 201
    flat = str(r.json()).lower()
    for leaked in ('12 march 2002', "'male'", 'indian'):            # the *values* of protected attributes never survive
        assert leaked not in flat
    assert {'date_of_birth', 'gender', 'nationality'} <= set(r.json()['parsed_data']['engine']['redacted_fields'])


def test_resume_endpoints_require_a_key_and_are_tenant_scoped(tenant):
    pdf, _ = midlevel_pdf()
    rid = upload(client(tenant.plaintext_key), pdf).json()['id']
    assert APIClient().post('/api/resume/upload/', {}).status_code in (401, 403)
    assert APIClient().get(f'/api/resume/{rid}/').status_code in (401, 403)
    assert client(tenant.plaintext_key).get(f'/api/resume/{rid}/').status_code == 200
    _, k2 = Tenant.create_with_key('Rival')
    assert client(k2).get(f'/api/resume/{rid}/').status_code == 404
    # and cannot start an interview on it either
    assert client(k2).post('/api/interview/start/', {'resume_id': rid}, format='json').status_code == 404


@override_settings(RESUME_ISOLATED_PARSE=True)
def test_isolated_subprocess_parse_path(tenant):
    pdf, _ = midlevel_pdf()
    r = upload(client(tenant.plaintext_key), pdf)
    assert r.status_code == 201 and r.json()['candidate_name'] == 'Marcus Chen'
