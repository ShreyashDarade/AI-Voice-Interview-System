"""
Core vocabulary of the proctoring framework.

Pipeline:  sensors -> features -> detectors -> Signals -> RiskEngine -> Decision

Everything in here is plain Python (no Django, no ML imports) so the whole
framework can be unit-tested and reasoned about in isolation.
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Mapping


class Severity(enum.IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class Category(str, enum.Enum):
    """Risk categories. Each category has its own cap in the risk score so no
    single noisy sensor can dominate a verdict."""
    IDENTITY = 'identity'          # who is on camera
    PRESENCE = 'presence'          # is a (single) person in front of the camera
    ATTENTION = 'attention'        # gaze / head pose
    ENVIRONMENT = 'environment'    # phones, books, second screens, lighting
    DEVICE = 'device'              # browser / OS level telemetry
    TAMPERING = 'tampering'        # feed manipulation, replay, injection
    BEHAVIOR = 'behavior'          # timing / audio-visual consistency
    CONNECTIVITY = 'connectivity'  # drops, reconnects


class Source(str, enum.Enum):
    VISION = 'vision'
    TELEMETRY = 'telemetry'
    AUDIO = 'audio'
    SYSTEM = 'system'


class Action(enum.IntEnum):
    """Escalation ladder. Ordered so that max() picks the harshest."""
    NONE = 0
    WARN = 1
    FLAG = 2        # mark session for human review, interview continues
    PAUSE = 3       # candidate must resolve (e.g. re-centre in frame)
    TERMINATE = 4


class Verdict(str, enum.Enum):
    PENDING = 'pending'
    CLEAR = 'clear'
    REVIEW = 'review'   # evidence a human should look at
    FAIL = 'fail'       # policy terminated the session


@dataclass(frozen=True, kw_only=True)
class Signal:
    """One observation emitted by a detector.

    A signal is *evidence*, not a verdict. The RiskEngine decides what it is
    worth under the active policy.
    """
    kind: str
    category: Category
    source: Source
    confidence: float                       # 0..1 how sure the detector is
    severity: Severity = Severity.LOW
    details: Mapping[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)   # server clock, epoch seconds
    capture_evidence: bool = False          # ask the orchestrator to keep a snapshot
    # Signals that describe a *state* (e.g. "no face") are re-emitted while the
    # state persists; the engine de-duplicates them by (kind, episode_key).
    episode_key: str = ''

    def __post_init__(self):
        if not 0.0 <= self.confidence <= 1.0:
            object.__setattr__(self, 'confidence', max(0.0, min(1.0, self.confidence)))


@dataclass
class Frame:
    """A decoded video frame plus transport metadata."""
    image: Any                    # numpy BGR uint8 array (H, W, 3)
    ts: float                     # server receive time (epoch seconds)
    seq: int = 0
    client_ts: float | None = None
    jpeg_size: int = 0
    #: per-frame hints from the pipeline to extractors (e.g. want_embedding)
    hints: dict = field(default_factory=dict)

    @property
    def shape(self):
        return self.image.shape


@dataclass
class Decision:
    """Outcome of one risk evaluation."""
    score: float                                  # 0..100
    action: Action
    verdict: Verdict
    strikes: int
    reasons: list[dict]                           # top contributors, explainable
    category_scores: dict[str, float]
    new_action: bool = False                      # True if action escalated this call
    message: str = ''                             # candidate-facing text (generic, no detector internals)
