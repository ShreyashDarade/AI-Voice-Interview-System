"""
Policy = every tunable decision in one immutable, serialisable object.

Three presets (lenient / standard / strict) plus per-session overrides and
accommodations. The policy that was in force is stored with every session so a
verdict is always reproducible and explainable.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from .types import Action, Category, Severity


POLICY_VERSION = '2026.10.1'


@dataclass(frozen=True)
class Rule:
    """How the risk engine treats one signal kind."""
    category: Category
    severity: Severity
    weight: float                 # points at confidence 1.0
    cooldown_s: float = 20.0      # same kind counted at most once per cooldown
    min_confidence: float = 0.5
    strike: bool = False          # counts towards max_strikes
    action: Action = Action.NONE  # hard override: minimum action when counted
    hint: str = ''                # candidate-facing, non-revealing guidance


# Candidate-facing hints are deliberately generic: they tell a person what to
# fix without teaching an adversary the thresholds.
_H_PRESENCE = 'Please make sure only you are visible and your face is clearly in frame.'
_H_ATTENTION = 'Please keep your attention on the interview screen.'
_H_ENV = 'Please remove other devices and reference materials from view.'
_H_DEVICE = 'Please stay in the interview window and avoid switching applications.'
_H_FEED = 'We could not verify your camera feed. Please use a standard, unmodified camera.'
_H_VIDEO = 'Please improve your lighting and camera position.'


def _r(cat, sev, weight, **kw):
    return Rule(category=cat, severity=sev, weight=weight, **kw)


C, S, A = Category, Severity, Action

BASE_RULES: dict[str, Rule] = {
    # ---- presence ---------------------------------------------------------
    'face_missing':        _r(C.PRESENCE, S.MEDIUM, 14, cooldown_s=15, strike=True, hint=_H_PRESENCE),
    'multiple_faces':      _r(C.PRESENCE, S.HIGH, 30, cooldown_s=20, strike=True, hint=_H_PRESENCE),
    'additional_person':   _r(C.PRESENCE, S.MEDIUM, 18, cooldown_s=30, hint=_H_PRESENCE),
    'face_out_of_frame':   _r(C.PRESENCE, S.LOW, 4, cooldown_s=30, min_confidence=0.6, hint=_H_PRESENCE),
    # ---- attention --------------------------------------------------------
    'gaze_off_screen':     _r(C.ATTENTION, S.LOW, 6, cooldown_s=20, hint=_H_ATTENTION),
    'head_turned_away':    _r(C.ATTENTION, S.LOW, 7, cooldown_s=20, hint=_H_ATTENTION),
    'looking_down':        _r(C.ATTENTION, S.LOW, 6, cooldown_s=30, hint=_H_ATTENTION),
    'reading_pattern':     _r(C.ATTENTION, S.MEDIUM, 16, cooldown_s=60, min_confidence=0.6, hint=_H_ATTENTION),
    'periodic_glance':     _r(C.ATTENTION, S.MEDIUM, 14, cooldown_s=90, min_confidence=0.6, hint=_H_ATTENTION),
    # ---- environment ------------------------------------------------------
    'phone_detected':      _r(C.ENVIRONMENT, S.HIGH, 28, cooldown_s=30, strike=True, hint=_H_ENV),
    'reference_material':  _r(C.ENVIRONMENT, S.MEDIUM, 14, cooldown_s=45, strike=True, hint=_H_ENV),
    'camera_blocked':      _r(C.ENVIRONMENT, S.HIGH, 22, cooldown_s=20, strike=True, hint=_H_VIDEO),
    'poor_video_quality':  _r(C.ENVIRONMENT, S.LOW, 3, cooldown_s=60, min_confidence=0.6, hint=_H_VIDEO),
    # ---- identity ---------------------------------------------------------
    'identity_changed':    _r(C.IDENTITY, S.CRITICAL, 60, cooldown_s=60, min_confidence=0.7,
                              strike=True, action=A.FLAG, hint=_H_PRESENCE),
    'identity_mismatch':   _r(C.IDENTITY, S.CRITICAL, 45, cooldown_s=120, min_confidence=0.7,
                              strike=True, action=A.FLAG, hint=_H_PRESENCE),
    # ---- tampering --------------------------------------------------------
    'frozen_feed':         _r(C.TAMPERING, S.HIGH, 25, cooldown_s=30, strike=True, hint=_H_FEED),
    'static_image_suspected': _r(C.TAMPERING, S.HIGH, 30, cooldown_s=60, min_confidence=0.65,
                                 strike=True, action=A.FLAG, hint=_H_FEED),
    'feed_loop_suspected': _r(C.TAMPERING, S.CRITICAL, 40, cooldown_s=120, min_confidence=0.7,
                              strike=True, action=A.FLAG, hint=_H_FEED),
    'frame_timing_anomaly': _r(C.TAMPERING, S.MEDIUM, 8, cooldown_s=60, hint=_H_FEED),
    'virtual_camera':      _r(C.TAMPERING, S.HIGH, 35, cooldown_s=300, min_confidence=0.6,
                              strike=True, action=A.FLAG, hint=_H_FEED),
    'camera_changed':      _r(C.TAMPERING, S.MEDIUM, 12, cooldown_s=60, hint=_H_FEED),
    'client_integrity':    _r(C.TAMPERING, S.HIGH, 25, cooldown_s=60, min_confidence=0.6, action=A.FLAG),
    'client_silent':       _r(C.TAMPERING, S.MEDIUM, 12, cooldown_s=60, hint=_H_DEVICE),
    # ---- device / browser telemetry --------------------------------------
    'tab_hidden':          _r(C.DEVICE, S.MEDIUM, 14, cooldown_s=10, strike=True, hint=_H_DEVICE),
    'window_blur':         _r(C.DEVICE, S.LOW, 6, cooldown_s=10, hint=_H_DEVICE),
    'fullscreen_exit':     _r(C.DEVICE, S.MEDIUM, 12, cooldown_s=10, strike=True, hint=_H_DEVICE),
    'clipboard_paste':     _r(C.DEVICE, S.MEDIUM, 12, cooldown_s=5, hint=_H_DEVICE),
    'clipboard_copy':      _r(C.DEVICE, S.LOW, 5, cooldown_s=5, hint=_H_DEVICE),
    'context_menu':        _r(C.DEVICE, S.INFO, 1, cooldown_s=5, min_confidence=0.0),
    'devtools_open':       _r(C.DEVICE, S.MEDIUM, 12, cooldown_s=60, min_confidence=0.6, hint=_H_DEVICE),
    'multiple_displays':   _r(C.DEVICE, S.MEDIUM, 14, cooldown_s=300, hint=_H_ENV),
    'remote_access_suspected': _r(C.DEVICE, S.HIGH, 24, cooldown_s=300, min_confidence=0.6,
                                  action=A.FLAG, hint=_H_DEVICE),
    'blocked_shortcut':    _r(C.DEVICE, S.LOW, 4, cooldown_s=10, hint=_H_DEVICE),
    'device_changed':      _r(C.DEVICE, S.MEDIUM, 15, cooldown_s=300, hint=_H_DEVICE),
    'concurrent_session':  _r(C.DEVICE, S.HIGH, 30, cooldown_s=120, action=A.FLAG),
    # ---- behaviour --------------------------------------------------------
    'av_desync':           _r(C.BEHAVIOR, S.HIGH, 24, cooldown_s=60, min_confidence=0.65,
                              action=A.FLAG, hint=_H_FEED),
    'latency_signature':   _r(C.BEHAVIOR, S.MEDIUM, 14, cooldown_s=180, min_confidence=0.6),
    # ---- connectivity -----------------------------------------------------
    'disconnected':        _r(C.CONNECTIVITY, S.LOW, 4, cooldown_s=30, min_confidence=0.0),
    'excessive_reconnects': _r(C.CONNECTIVITY, S.MEDIUM, 10, cooldown_s=300, min_confidence=0.0),
}

DEFAULT_CATEGORY_CAPS: dict[str, float] = {
    Category.IDENTITY.value: 100, Category.TAMPERING.value: 70, Category.PRESENCE.value: 50,
    Category.ENVIRONMENT.value: 45, Category.DEVICE.value: 40, Category.ATTENTION.value: 30,
    Category.BEHAVIOR.value: 35, Category.CONNECTIVITY.value: 15,
}

FRAME_DETECTORS = (
    'video_quality', 'face_presence', 'gaze', 'head_pose', 'reading_pattern', 'periodic_glance',
    'objects', 'identity', 'feed_integrity',
)
EVENT_DETECTORS = ('browser_telemetry', 'client_integrity', 'av_sync', 'response_latency', 'connectivity')

#: accommodation -> detectors / rule kinds it switches off
ACCOMMODATIONS: dict[str, dict[str, tuple[str, ...]]] = {
    # candidates who cannot hold a typical gaze/posture (motor, vision, ADHD, ...)
    'relaxed_gaze': {'detectors': ('gaze', 'head_pose', 'reading_pattern', 'periodic_glance'),
                     'kinds': ('gaze_off_screen', 'head_turned_away', 'looking_down',
                               'reading_pattern', 'periodic_glance')},
    # screen-reader / assistive tooling legitimately uses clipboard & shortcuts
    'assistive_technology': {'detectors': (),
                             'kinds': ('clipboard_copy', 'clipboard_paste', 'blocked_shortcut',
                                       'remote_access_suspected', 'window_blur')},
    # needs to look at notes for medical / language reasons
    'reference_notes': {'detectors': ('reading_pattern', 'periodic_glance'),
                        'kinds': ('looking_down', 'reading_pattern', 'periodic_glance',
                                  'reference_material')},
    # bathroom breaks, caregivers in the room
    'shared_space': {'detectors': (),
                     'kinds': ('additional_person', 'face_missing', 'face_out_of_frame')},
}


@dataclass(frozen=True)
class Policy:
    name: str
    rules: Mapping[str, Rule]
    category_caps: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_CATEGORY_CAPS))
    # score half-life: how fast old incidents stop counting towards *live* actions
    half_life_s: float = 300.0
    # live-score thresholds
    warn_at: float = 20.0
    flag_at: float = 40.0
    terminate_at: float = 85.0
    # final verdict thresholds use the *undecayed* cumulative score
    review_at: float = 30.0
    fail_at: float = 85.0
    max_strikes: int = 3
    strikes_terminate: bool = True
    grace_period_s: float = 20.0          # no soft scoring while camera/baseline settle
    calibration_s: float = 8.0            # seconds of clean frames to learn the candidate's neutral pose
    heartbeat_timeout_s: float = 30.0
    disconnect_grace_s: float = 90.0      # session may be resumed within this window
    max_reconnects: int = 6
    max_evidence_per_session: int = 40
    evidence_min_interval_s: float = 10.0
    frame_rate_hz: float = 2.0            # target analysis rate (client is told this)
    require_identity_reference: bool = False
    detectors: tuple[str, ...] = FRAME_DETECTORS + EVENT_DETECTORS
    detector_config: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    accommodations: tuple[str, ...] = ()

    # -- helpers -----------------------------------------------------------
    def rule(self, kind: str) -> Rule | None:
        return self.rules.get(kind)

    def with_overrides(self, overrides: Mapping[str, Any] | None) -> 'Policy':
        """Apply a sparse dict of overrides (from the integrator's API call)."""
        if not overrides:
            return self
        allowed = {'warn_at', 'flag_at', 'terminate_at', 'review_at', 'fail_at', 'max_strikes',
                   'strikes_terminate', 'grace_period_s', 'calibration_s', 'heartbeat_timeout_s',
                   'disconnect_grace_s', 'max_reconnects', 'frame_rate_hz', 'half_life_s',
                   'require_identity_reference', 'max_evidence_per_session'}
        kwargs = {k: v for k, v in overrides.items() if k in allowed}
        pol = replace(self, **kwargs)
        if 'detector_config' in overrides:
            merged = copy.deepcopy(dict(self.detector_config))
            for det, conf in overrides['detector_config'].items():
                merged.setdefault(det, {}).update(conf)
            pol = replace(pol, detector_config=merged)
        if 'disable_detectors' in overrides:
            off = set(overrides['disable_detectors'])
            pol = replace(pol, detectors=tuple(d for d in pol.detectors if d not in off))
        if 'rule_weights' in overrides:
            rules = dict(pol.rules)
            for kind, w in overrides['rule_weights'].items():
                if kind in rules:
                    rules[kind] = replace(rules[kind], weight=float(w))
            pol = replace(pol, rules=rules)
        if overrides.get('accommodations'):
            pol = pol.with_accommodations(overrides['accommodations'])
        return pol

    def with_accommodations(self, names) -> 'Policy':
        names = tuple(sorted(set(self.accommodations) | set(names)))
        unknown = [n for n in names if n not in ACCOMMODATIONS]
        if unknown:
            raise ValueError(f'Unknown accommodation(s): {unknown}')
        off_det, off_kind = set(), set()
        for n in names:
            off_det.update(ACCOMMODATIONS[n]['detectors'])
            off_kind.update(ACCOMMODATIONS[n]['kinds'])
        rules = {k: v for k, v in self.rules.items() if k not in off_kind}
        dets = tuple(d for d in self.detectors if d not in off_det)
        return replace(self, rules=rules, detectors=dets, accommodations=names)

    def to_dict(self) -> dict:
        return {
            'name': self.name, 'version': POLICY_VERSION,
            'half_life_s': self.half_life_s, 'warn_at': self.warn_at, 'flag_at': self.flag_at,
            'terminate_at': self.terminate_at, 'review_at': self.review_at, 'fail_at': self.fail_at,
            'max_strikes': self.max_strikes, 'strikes_terminate': self.strikes_terminate,
            'grace_period_s': self.grace_period_s, 'calibration_s': self.calibration_s,
            'heartbeat_timeout_s': self.heartbeat_timeout_s, 'disconnect_grace_s': self.disconnect_grace_s,
            'max_reconnects': self.max_reconnects, 'frame_rate_hz': self.frame_rate_hz,
            'require_identity_reference': self.require_identity_reference,
            'detectors': list(self.detectors),
            'detector_config': {k: dict(v) for k, v in self.detector_config.items()},
            'accommodations': list(self.accommodations),
            'category_caps': dict(self.category_caps),
            'rules': {k: {'category': r.category.value, 'severity': int(r.severity), 'weight': r.weight,
                          'cooldown_s': r.cooldown_s, 'min_confidence': r.min_confidence,
                          'strike': r.strike, 'action': int(r.action)} for k, r in self.rules.items()},
        }

    def fingerprint(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _scaled(rules: Mapping[str, Rule], scale: float, overrides: Mapping[str, dict] | None = None) -> dict[str, Rule]:
    out = {}
    for k, r in rules.items():
        kw = {'weight': round(r.weight * scale, 2), **(overrides or {}).get(k, {})}   # explicit override wins
        out[k] = replace(r, **kw)
    return out


def lenient() -> Policy:
    """Practice rounds, low-stakes screening, accessibility-first deployments."""
    overrides = {k: {'strike': False} for k, r in BASE_RULES.items() if r.strike}
    for k in ('identity_changed', 'multiple_faces', 'phone_detected', 'static_image_suspected',
              'feed_loop_suspected', 'virtual_camera'):
        overrides[k] = {'strike': True}
    return Policy(
        name='lenient', rules=_scaled(BASE_RULES, 0.6, overrides),
        warn_at=30, flag_at=55, terminate_at=100, review_at=40, fail_at=100,
        max_strikes=5, strikes_terminate=False, grace_period_s=30, half_life_s=240,
        detector_config={
            'face_presence': {'min_missing_s': 8.0, 'min_multi_s': 4.0},
            'gaze': {'min_duration_s': 6.0}, 'head_pose': {'min_duration_s': 6.0},
            'objects': {'min_hits': 4, 'window': 6},
        },
    )


def standard() -> Policy:
    """Default for hiring screens."""
    return Policy(
        name='standard', rules=dict(BASE_RULES),
        detector_config={
            'face_presence': {'min_missing_s': 4.0, 'min_multi_s': 2.0},
            'gaze': {'min_duration_s': 3.5}, 'head_pose': {'min_duration_s': 3.5},
            'objects': {'min_hits': 3, 'window': 5},
        },
    )


def strict() -> Policy:
    """Certification / high-stakes hiring: lower tolerance, faster escalation."""
    overrides = {
        'virtual_camera': {'action': A.TERMINATE},
        'identity_changed': {'action': A.TERMINATE},
        'feed_loop_suspected': {'action': A.TERMINATE},
        'multiple_faces': {'weight': 36.0},
        'phone_detected': {'weight': 34.0},
        'window_blur': {'strike': True},
        'clipboard_paste': {'strike': True},
    }
    return Policy(
        name='strict', rules=_scaled(BASE_RULES, 1.25, overrides),
        warn_at=15, flag_at=30, terminate_at=70, review_at=20, fail_at=70,
        max_strikes=2, grace_period_s=15, half_life_s=420,
        require_identity_reference=False,
        detector_config={
            'face_presence': {'min_missing_s': 2.5, 'min_multi_s': 1.5},
            'gaze': {'min_duration_s': 2.5}, 'head_pose': {'min_duration_s': 2.5},
            'objects': {'min_hits': 2, 'window': 4},
        },
    )


PRESETS = {'lenient': lenient, 'standard': standard, 'strict': strict}


def get_policy(name: str = 'standard', overrides: Mapping[str, Any] | None = None) -> Policy:
    try:
        base = PRESETS[name]()
    except KeyError:
        raise ValueError(f'Unknown policy preset {name!r}; choose from {sorted(PRESETS)}')
    return base.with_overrides(overrides)
