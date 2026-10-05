"""
Risk engine: turns a stream of Signals into an explainable score, an action
and a final verdict.

Design rules
------------
* **Evidence accumulates, noise decays.** The live score uses exponential
  decay (half-life from policy) so a transient glance fades, while the
  *cumulative* score (no decay) feeds the end-of-session verdict so a
  candidate cannot "wait out" an incident.
* **Per-category caps.** One flaky sensor can never reach the termination
  threshold on its own unless a rule is explicitly marked as a hard action.
* **Cooldowns.** A persistent state (e.g. phone on the desk) is counted once
  per cooldown window, not once per frame.
* **Sticky escalation.** Actions never de-escalate within a session.
* **Deterministic & replayable.** State is a list of ``CountedEvent`` -- the
  engine can be rebuilt from the database after a reconnect or worker restart.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Iterable

from .policy import Policy
from .types import Action, Category, Decision, Severity, Signal, Verdict


@dataclass
class CountedEvent:
    kind: str
    category: Category
    severity: Severity
    points: float            # weight * confidence, before decay
    ts: float
    strike: bool
    confidence: float
    event_id: str = ''       # link back to the persisted ProctorEvent


class RiskEngine:
    def __init__(self, policy: Policy, started_at: float | None = None, floor: Action = Action.NONE):
        self.policy = policy
        self.started_at = started_at
        self.events: list[CountedEvent] = []
        self._last_counted: dict[str, float] = {}
        self._floor = floor                      # sticky action
        self._hard_floor = Action.NONE

    # -- state ------------------------------------------------------------
    def start(self, ts: float | None = None) -> None:
        if self.started_at is None:
            self.started_at = ts if ts is not None else time.time()

    @property
    def floor(self) -> Action:
        return self._floor

    @property
    def strikes(self) -> int:
        return sum(1 for e in self.events if e.strike)

    def replay(self, events: Iterable[CountedEvent]) -> None:
        for e in events:
            self.events.append(e)
            self._last_counted[e.kind] = max(self._last_counted.get(e.kind, 0.0), e.ts)
            rule = self.policy.rule(e.kind)
            if rule and rule.action > self._hard_floor:
                self._hard_floor = rule.action

    # -- ingest -----------------------------------------------------------
    def ingest(self, signal: Signal) -> CountedEvent | None:
        """Count a signal if the policy says it matters. Returns the counted
        event (to persist) or None when ignored/suppressed."""
        rule = self.policy.rule(signal.kind)
        if rule is None or signal.confidence < rule.min_confidence:
            return None
        last = self._last_counted.get(signal.kind)
        if last is not None and signal.ts - last < rule.cooldown_s:
            return None

        points = rule.weight * signal.confidence
        in_grace = (self.started_at is not None
                    and signal.ts - self.started_at < self.policy.grace_period_s)
        # Grace only mutes *soft* signals; hard actions and identity/tamper always count.
        if in_grace and rule.action == Action.NONE and rule.category not in (Category.IDENTITY, Category.TAMPERING):
            return None

        ev = CountedEvent(kind=signal.kind, category=rule.category, severity=rule.severity,
                          points=points, ts=signal.ts, strike=rule.strike, confidence=signal.confidence)
        self.events.append(ev)
        self._last_counted[signal.kind] = signal.ts
        if rule.action > self._hard_floor:
            self._hard_floor = rule.action
        return ev

    # -- scoring ----------------------------------------------------------
    def _category_totals(self, now: float, decay: bool) -> dict[str, float]:
        totals: dict[str, float] = {}
        hl = self.policy.half_life_s
        for e in self.events:
            w = e.points
            if decay and hl > 0:
                w *= 0.5 ** (max(0.0, now - e.ts) / hl)
            totals[e.category.value] = totals.get(e.category.value, 0.0) + w
        caps = self.policy.category_caps
        return {c: min(v, caps.get(c, 100.0)) for c, v in totals.items()}

    def live_score(self, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        return min(100.0, sum(self._category_totals(now, True).values()))

    def cumulative_score(self) -> float:
        return min(100.0, sum(self._category_totals(0, False).values()))

    def explain(self, top: int = 6) -> list[dict]:
        groups: dict[str, dict] = {}
        for e in self.events:
            g = groups.setdefault(e.kind, {'kind': e.kind, 'category': e.category.value,
                                           'severity': int(e.severity), 'count': 0, 'points': 0.0,
                                           'first_ts': e.ts, 'last_ts': e.ts, 'max_confidence': 0.0})
            g['count'] += 1
            g['points'] = round(g['points'] + e.points, 2)
            g['first_ts'] = min(g['first_ts'], e.ts)
            g['last_ts'] = max(g['last_ts'], e.ts)
            g['max_confidence'] = round(max(g['max_confidence'], e.confidence), 3)
        return sorted(groups.values(), key=lambda g: g['points'], reverse=True)[:top]

    # -- decision ---------------------------------------------------------
    def evaluate(self, now: float | None = None) -> Decision:
        now = now if now is not None else time.time()
        p = self.policy
        cats = self._category_totals(now, True)
        score = min(100.0, sum(cats.values()))
        strikes = self.strikes

        action = self._hard_floor
        if score >= p.terminate_at:
            action = max(action, Action.TERMINATE)
        elif score >= p.flag_at:
            action = max(action, Action.FLAG)
        elif score >= p.warn_at:
            action = max(action, Action.WARN)
        if p.strikes_terminate and strikes >= p.max_strikes:
            action = max(action, Action.TERMINATE)
        elif strikes >= 1:
            action = max(action, Action.WARN)
        if strikes >= max(1, p.max_strikes - 1) and p.strikes_terminate:
            action = max(action, Action.FLAG)

        escalated = action > self._floor
        if escalated:
            self._floor = action
        action = max(action, self._floor)

        return Decision(
            score=round(score, 2), action=action, verdict=self.verdict(action=action),
            strikes=strikes, reasons=self.explain(), category_scores={k: round(v, 2) for k, v in cats.items()},
            new_action=escalated, message=self.candidate_message(action, escalated),
        )

    def verdict(self, action: Action | None = None) -> Verdict:
        action = self._floor if action is None else max(action, self._floor)
        if action >= Action.TERMINATE:
            return Verdict.FAIL
        cum = self.cumulative_score()
        if (action >= Action.FLAG or cum >= self.policy.review_at
                or any(e.severity >= Severity.HIGH for e in self.events)):
            return Verdict.REVIEW
        return Verdict.CLEAR

    def candidate_message(self, action: Action, escalated: bool) -> str:
        """Generic, non-revealing text. Never names a detector or threshold."""
        if not escalated or action == Action.NONE:
            return ''
        last = self.events[-1] if self.events else None
        rule = self.policy.rule(last.kind) if last else None
        hint = (rule.hint if rule else '') or 'Please follow the interview rules.'
        remaining = max(0, self.policy.max_strikes - self.strikes)
        if action >= Action.TERMINATE:
            return 'This interview has ended because it could not be completed under the interview rules.'
        if action == Action.FLAG and self.policy.strikes_terminate and remaining:
            return f'Final notice. {hint}'
        return f'Notice. {hint}'
