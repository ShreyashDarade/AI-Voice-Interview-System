import time

import cv2
import numpy as np
import pytest
from django.core.files.base import ContentFile

from core.models import Interview, Resume
from proctoring import services
from proctoring.framework.types import Category, Severity, Signal, Source
from proctoring.models import Tenant


def make_jpeg(w=320, h=240, seed=0) -> bytes:
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
    ok, buf = cv2.imencode('.jpg', img)
    return buf.tobytes()


def make_signal(kind, conf=0.9, ts=None, cat=Category.ENVIRONMENT, sev=Severity.HIGH, evidence=False, **details):
    return Signal(kind=kind, category=cat, severity=sev, confidence=conf, source=Source.VISION,
                  ts=ts if ts is not None else time.time(), details=details, capture_evidence=evidence)


@pytest.fixture
def tenant(db):
    t, key = Tenant.create_with_key('Acme ATS', default_policy='standard')
    t.plaintext_key = key
    return t


@pytest.fixture
def resume(db):
    r = Resume(original_filename='cv.pdf', candidate_name='Ada Lovelace', skills=['Python'])
    r.file.save('cv.pdf', ContentFile(b'%PDF-1.4 test'), save=False)
    r.save()
    return r


@pytest.fixture
def interview(resume):
    return Interview.objects.create(resume=resume, experience_level='mid')


@pytest.fixture
def make_session(db, resume):
    def _make(policy='standard', tenant=None, **kw):
        r = resume
        if Interview.objects.filter(resume=r).exists():
            r = Resume.objects.create(original_filename='cv2.pdf', candidate_name='Other', file='resumes/x.pdf')
        i = Interview.objects.create(resume=r, experience_level='mid')
        return services.create_session(interview=i, tenant=tenant, policy_name=policy, **kw)
    return _make


@pytest.fixture
def active_session(make_session):
    s = make_session()
    services.record_consent(s.id, ip='10.0.0.1')
    return services.start_session(s.id, ip='10.0.0.1', user_agent='UA/1')
