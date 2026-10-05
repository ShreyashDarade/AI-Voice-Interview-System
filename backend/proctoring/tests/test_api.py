import json
import time

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from core.models import Interview, Resume
from proctoring import services
from proctoring.models import ProctorSession, Tenant

from proctoring.testing import make_jpeg, make_signal

pytestmark = pytest.mark.django_db


def api(key=None, token=None):
    c = APIClient()
    if key:
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {key}')
    elif token:
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return c


@pytest.fixture
def integ(tenant):
    return api(key=tenant.plaintext_key)


def create(integ, interview, **body):
    return integ.post('/api/proctor/sessions/', {'interview_id': str(interview.id), **body}, format='json')


# ============================================================================ auth
def test_requires_api_key(interview):
    assert create(APIClient(), interview).status_code in (401, 403)
    assert create(api(key='px_deadbeef_wrong'), interview).status_code == 401


def test_candidate_token_cannot_use_integrator_endpoints(integ, interview):
    creds = create(integ, interview).json()
    r = api(token=creds['candidate_token']).get(f"/api/proctor/sessions/{creds['session_id']}/report/")
    assert r.status_code in (401, 403)


def test_inactive_tenant_key_rejected(tenant, interview):
    tenant.is_active = False; tenant.save()
    assert create(api(key=tenant.plaintext_key), interview).status_code == 401


# ============================================================================ create + isolation
def test_create_session_returns_credentials_and_policy(integ, interview):
    r = create(integ, interview, policy='strict', accommodations=['relaxed_gaze'], external_ref='cand-77')
    assert r.status_code == 201
    b = r.json()
    assert b['state'] == 'created' and b['candidate_token'].startswith('v1.') and b['telemetry_key']
    assert b['ws_path'] == f"/ws/proctor/{b['session_id']}/" and b['frame_rate_hz'] > 0
    s = ProctorSession.objects.get(pk=b['session_id'])
    assert s.policy_name == 'strict' and s.accommodations == ['relaxed_gaze'] and s.external_ref == 'cand-77'
    assert create(integ, interview).status_code == 409                       # one session per interview


def test_invalid_inputs(integ, interview):
    assert create(integ, interview, policy='nope').status_code == 400
    assert create(integ, interview, accommodations=['x']).status_code == 400
    r = integ.post('/api/proctor/sessions/', {'interview_id': '00000000-0000-0000-0000-000000000000'}, format='json')
    assert r.status_code == 404


def test_tenants_cannot_see_each_others_sessions(integ, interview):
    sid = create(integ, interview).json()['session_id']
    other, key = Tenant.create_with_key('Other Co')
    for path in ('', 'report/', 'events/'):
        assert api(key=key).get(f'/api/proctor/sessions/{sid}/{path}').status_code == 404
    assert api(key=key).post(f'/api/proctor/sessions/{sid}/terminate/', {}, format='json').status_code == 404
    assert integ.get(f'/api/proctor/sessions/{sid}/').status_code == 200


# ============================================================================ candidate flow
def test_full_candidate_flow_and_report(integ, interview):
    creds = create(integ, interview, policy='standard').json()
    sid, cand = creds['session_id'], api(token=creds['candidate_token'])
    base = f'/api/proctor/sessions/{sid}'

    assert cand.post(f'{base}/start/', {}, format='json').status_code == 412          # consent first
    assert cand.post(f'{base}/consent/', {'accepted': False}, format='json').status_code == 412
    assert cand.post(f'{base}/consent/', {'accepted': True}, format='json').json()['state'] == 'preflight'
    started = cand.post(f'{base}/start/', {}, format='json')
    assert started.status_code == 200 and started.json()['state'] == 'active'
    Interview.objects.get(pk=interview.pk).status == 'in_progress'

    services.apply_signals(sid, [make_signal('phone_detected', ts=time.time() + 60)])
    st = integ.get(f'{base}/').json()
    assert st['strikes'] == 1 and st['verdict'] == 'review' and st['risk_score'] > 0

    ev = integ.get(f'{base}/events/').json()['events']
    assert [e['kind'] for e in ev][-1] == 'phone_detected' and all(len(e['hash']) == 64 for e in ev)

    assert cand.post(f'{base}/complete/', {}, format='json').json()['state'] == 'completed'
    rep = integ.get(f'{base}/report/').json()
    assert rep['session']['verdict'] == 'review' and rep['integrity']['chain_ok']
    assert rep['summary']['signals_by_kind'] == {'phone_detected': 1}


def test_candidate_token_is_scoped_to_its_own_session(integ, resume, interview):
    a = create(integ, interview).json()
    r2 = Resume.objects.create(original_filename='b.pdf', candidate_name='B', file='resumes/b.pdf')
    i2 = Interview.objects.create(resume=r2, experience_level='junior')
    b = create(integ, i2).json()
    cand_a = api(token=a['candidate_token'])
    assert cand_a.post(f"/api/proctor/sessions/{b['session_id']}/consent/", {'accepted': True}, format='json').status_code in (401, 403)


def test_expired_candidate_token_rejected(integ, interview, settings):
    from proctoring.framework import tokens
    sid = create(integ, interview).json()['session_id']
    old = tokens.issue(settings.PROCTOR['TOKEN_SECRET'], sid, 'candidate', 10, now=time.time() - 100)
    assert api(token=old).post(f'/api/proctor/sessions/{sid}/consent/', {'accepted': True}, format='json').status_code == 401


def test_preflight_endpoint_validates_upload(integ, interview, settings):
    creds = create(integ, interview).json()
    c = api(token=creds['candidate_token'])
    url = f"/api/proctor/sessions/{creds['session_id']}/preflight/"
    assert c.post(url, {}, format='multipart').status_code == 400
    r = c.post(url, {'frame': SimpleUploadedFile('f.jpg', b'not a jpeg', 'image/jpeg')}, format='multipart')
    assert r.status_code == 400 or r.json().get('issues') == ['camera_unavailable']     # bad bytes, or no models installed
    big = SimpleUploadedFile('f.jpg', b'x' * (settings.PROCTOR['MAX_FRAME_BYTES'] + 1), 'image/jpeg')
    assert c.post(url, {'frame': big}, format='multipart').status_code == 400


# ============================================================================ terminate / erase / catalog
def test_terminate_then_erase(integ, interview):
    sid = create(integ, interview).json()['session_id']
    assert integ.delete(f'/api/proctor/sessions/{sid}/').status_code == 409          # cannot erase a live session
    r = integ.post(f'/api/proctor/sessions/{sid}/terminate/', {'reason': 'proctor saw second person'}, format='json')
    assert r.json() == {'state': 'terminated', 'verdict': 'fail'}
    assert integ.delete(f'/api/proctor/sessions/{sid}/').status_code == 204
    assert not ProctorSession.objects.filter(pk=sid).exists()


def test_policy_catalog_and_capabilities(integ):
    cat = integ.get('/api/proctor/policies/').json()
    assert set(cat['presets']) == {'lenient', 'standard', 'strict'} and 'relaxed_gaze' in cat['accommodations']
    assert cat['presets']['strict']['rules']['phone_detected']['weight'] > cat['presets']['lenient']['rules']['phone_detected']['weight']
    caps = integ.get('/api/proctor/capabilities/').json()
    assert 'face_presence' in caps['detectors'] and 'multiple_faces' in caps['detectors']['face_presence']


def test_unauthenticated_catalog_blocked():
    assert APIClient().get('/api/proctor/policies/').status_code in (401, 403)


# ============================================================================ legacy interview endpoint
def test_interview_start_creates_pending_interview_and_proctor_session(tenant, resume):
    resume.owner_id = tenant.id; resume.save()
    r = api(key=tenant.plaintext_key).post('/api/interview/start/', {
        'resume_id': str(resume.id), 'experience_level': 'senior', 'policy': 'strict'}, format='json')
    assert r.status_code == 201, r.content
    b = r.json()
    assert b['status'] == 'pending' and b['proctor_session_id'] and b['candidate_token'] and b['voice_ws_path'].startswith('/ws/interview/')
    assert ProctorSession.objects.get(pk=b['proctor_session_id']).policy_name == 'strict'
    again = api(key=tenant.plaintext_key).post('/api/interview/start/', {'resume_id': str(resume.id)}, format='json')
    assert again.status_code == 409


def test_interview_start_refuses_another_tenants_resume(tenant, resume):
    resume.owner_id = tenant.id; resume.save()
    _, other = Tenant.create_with_key('Rival')
    r = api(key=other).post('/api/interview/start/', {'resume_id': str(resume.id)}, format='json')
    assert r.status_code == 404


def test_interview_start_requires_api_key(resume):
    r = APIClient().post('/api/interview/start/', {'resume_id': str(resume.id)}, format='json')
    assert r.status_code in (401, 403)


def test_evaluate_endpoint_is_tenant_scoped_and_labelled_for_human_review(integ, tenant, resume):
    resume.owner_id = tenant.id; resume.save()
    r = api(key=tenant.plaintext_key).post('/api/interview/start/', {'resume_id': str(resume.id)}, format='json').json()
    iid = r['id']
    assert integ.post(f'/api/interview/{iid}/evaluate/').status_code == 409          # not started yet
    sid = r['proctor_session_id']
    cand = api(token=r['candidate_token'])
    cand.post(f'/api/proctor/sessions/{sid}/consent/', {'accepted': True}, format='json')
    cand.post(f'/api/proctor/sessions/{sid}/start/', {}, format='json')
    out = integ.post(f'/api/interview/{iid}/evaluate/')
    assert out.status_code == 200 and out.json()['source'] == 'rules' and out.json()['human_review_required'] is True
    assert Interview.objects.get(pk=iid).evaluation['schema'] == 'interview-evaluation/1'
    _, other_key = Tenant.create_with_key('Rival')
    assert api(key=other_key).post(f'/api/interview/{iid}/evaluate/').status_code == 404
