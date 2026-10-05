"""Docs must not drift from the code: every detector and every emitted signal kind is catalogued."""
from pathlib import Path

import proctoring.detectors  # noqa: F401
from proctoring.framework.detector import registered

DOC = (Path(__file__).resolve().parents[3] / 'docs' / 'ARCHITECTURE.md').read_text()


def test_every_detector_and_signal_kind_is_documented():
    missing = []
    for name, cls in registered().items():
        if f'`{name}`' not in DOC:
            missing.append(name)
        missing += [k for k in cls.emits if f'`{k}`' not in DOC]
    assert not missing, f'undocumented in docs/ARCHITECTURE.md: {sorted(set(missing))}'
