"""
Detector plugin API + registry.

A detector is a small, stateful, per-session object. Adding a new cheating
signal to the platform means writing one class and decorating it with
``@register`` -- nothing else changes.

Two families:

* ``FrameDetector``  - consumes ``FrameContext`` (frame + extracted features)
* ``EventDetector``  - consumes discrete events (client telemetry, audio VAD,
                       lifecycle events) as ``Event`` objects
"""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar, Iterable, Mapping

from .types import Frame, Signal

logger = logging.getLogger(__name__)


@dataclass
class FrameContext:
    """What a frame detector sees: the frame, shared features, and baseline."""
    frame: Frame
    features: dict[str, Any] = field(default_factory=dict)
    baseline: Mapping[str, float] = field(default_factory=dict)
    config: Mapping[str, Any] = field(default_factory=dict)

    def get(self, key, default=None):
        return self.features.get(key, default)


@dataclass(frozen=True)
class Event:
    """A discrete, non-video event."""
    kind: str                       # e.g. 'visibility', 'clipboard', 'vad', 'turn'
    ts: float
    data: Mapping[str, Any] = field(default_factory=dict)
    origin: str = 'client'          # 'client' (untrusted) | 'server' (our own components)


class Detector(abc.ABC):
    """Base class for all detectors."""
    #: unique registry name, also used for per-policy enable/disable
    name: ClassVar[str]
    #: signal kinds this detector may emit (documentation + policy validation)
    emits: ClassVar[tuple[str, ...]] = ()
    #: feature keys that must be present in FrameContext.features, else skipped
    requires: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, config: Mapping[str, Any] | None = None):
        self.config = dict(config or {})

    def reset(self) -> None:  # pragma: no cover - default no-op
        """Drop internal state (e.g. after a pause)."""

    # Optional persistence so a candidate who reconnects keeps e.g. their
    # identity template. Must be JSON-serialisable.
    def export_state(self) -> dict | None:
        return None

    def import_state(self, state: Mapping[str, Any]) -> None:  # pragma: no cover
        pass


class FrameDetector(Detector):
    @abc.abstractmethod
    def process(self, ctx: FrameContext) -> list[Signal]:
        ...

    def hints(self, ts: float) -> dict:
        """Ask the extractor for optional (expensive) features on the next frame."""
        return {}


class EventDetector(Detector):
    #: event kinds this detector wants to see; empty = all
    handles: ClassVar[frozenset[str]] = frozenset()

    @abc.abstractmethod
    def process(self, event: Event) -> list[Signal]:
        ...

    def tick(self, now: float) -> list[Signal]:
        """Called periodically so detectors can emit time-based signals
        (e.g. 'heartbeat missing'). Default: nothing."""
        return []

    def on_activity(self, ts: float, kind: str, origin: str) -> None:
        """Called for *every* event/frame (before filtering by ``handles``)."""


class FeatureExtractor(abc.ABC):
    """Computes expensive shared features once per frame (landmarks, objects...).

    Extractors are process-wide singletons (they own ML models), unlike
    detectors which are per session.
    """
    name: ClassVar[str]
    provides: ClassVar[frozenset[str]] = frozenset()

    @abc.abstractmethod
    def extract(self, frame: Frame) -> dict[str, Any]:
        ...

    def close(self) -> None:  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

_DETECTORS: dict[str, type[Detector]] = {}


def register(cls: type[Detector]) -> type[Detector]:
    if not getattr(cls, 'name', None):
        raise TypeError(f'{cls.__name__} must define a `name`')
    if cls.name in _DETECTORS and _DETECTORS[cls.name] is not cls:
        raise ValueError(f'Duplicate detector name: {cls.name}')
    _DETECTORS[cls.name] = cls
    return cls


def registered() -> dict[str, type[Detector]]:
    return dict(_DETECTORS)


def build_detectors(names: Iterable[str], configs: Mapping[str, Mapping[str, Any]] | None = None) -> list[Detector]:
    configs = configs or {}
    out = []
    for n in names:
        cls = _DETECTORS.get(n)
        if cls is None:
            logger.warning('Unknown detector %r in policy - skipped', n)
            continue
        out.append(cls(configs.get(n)))
    return out
