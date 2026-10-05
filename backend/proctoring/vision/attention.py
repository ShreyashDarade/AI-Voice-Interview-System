"""
Derived attention features: how far is the candidate's head + eye direction
from *their own* calibrated neutral pose?

Honest limits: eye direction from blendshapes is a coarse proxy for gaze angle
(webcam-class accuracy, roughly +/-10 deg). We therefore combine it with head
pose, require sustained deviation (seconds, not frames), and let the risk
engine weigh the result as *evidence for review*, never as proof.
"""
from __future__ import annotations

import math

from .baseline import Baseline

EYE_H_DEG = 25.0     # +/-1.0 horizontal blendshape gaze ~ this many degrees
EYE_V_DEG = 15.0


def derive(primary, baseline: Baseline, cfg: dict | None = None) -> dict:
    cfg = cfg or {}
    yaw_lim = cfg.get('yaw_limit_deg', 25.0)
    up_lim = cfg.get('pitch_up_limit_deg', 22.0)
    down_lim = cfg.get('pitch_down_limit_deg', 20.0)
    # widen limits for candidates whose own baseline is noisy (fidgety, low fps)
    yaw_lim += 2.0 * baseline.spread.get('yaw', 0.0)
    up_lim += 2.0 * baseline.spread.get('pitch', 0.0)
    down_lim += 2.0 * baseline.spread.get('pitch', 0.0)

    yaw_dev = primary.yaw - baseline.get('yaw')
    pitch_dev = primary.pitch - baseline.get('pitch')
    eye_h = primary.gaze_h - baseline.get('gaze_h')
    eye_v = primary.gaze_v - baseline.get('gaze_v')

    eff_yaw = yaw_dev + EYE_H_DEG * eye_h
    eff_pitch = pitch_dev + EYE_V_DEG * eye_v

    def norm(y, p):
        return math.hypot(y / yaw_lim, p / (down_lim if p < 0 else up_lim))

    return {
        'yaw_dev': yaw_dev, 'pitch_dev': pitch_dev, 'eye_h_dev': eye_h, 'eye_v_dev': eye_v,
        'eff_yaw': eff_yaw, 'eff_pitch': eff_pitch,
        'gaze_score': norm(eff_yaw, eff_pitch),     # >= 1.0 => looking off-screen
        'head_score': norm(yaw_dev, pitch_dev),     # >= 1.0 => head turned away
    }
