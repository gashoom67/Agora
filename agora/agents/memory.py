from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Tuple

from agora.agents.persona import Persona
from agora.environments.base import Notice


@dataclass
class Relation:
    trust: float = 0.0
    interactions: int = 0
    positive: int = 0
    negative: int = 0
    last_note: str = ""
    last_step: int = 0


@dataclass
class Grievance:
    step: int
    other: str
    what: str
    amount: float


@dataclass
class AgentMemory:
    persona: Persona
    short_term: Deque[Tuple[int, str]] = field(default_factory=lambda: deque(maxlen=24))
    thoughts: Deque[Tuple[int, str]] = field(default_factory=lambda: deque(maxlen=3))
    relations: Dict[str, Relation] = field(default_factory=dict)
    grievances: List[Grievance] = field(default_factory=list)
    action_feedback: List[str] = field(default_factory=list)
    strategy: str = ""
    mood: str = "steady"

    def __post_init__(self) -> None:
        if not self.strategy:
            self.strategy = self.persona.objective

    def alpha(self, v: float) -> float:
        t = self.persona.traits
        return 0.15 + 0.2 * t["agreeableness"] if v >= 0 else 0.3 + 0.6 * t["vindictiveness"]

    def update_trust(self, other: str, v: float, step: int, note: str = "") -> float:
        r = self.relations.setdefault(other, Relation())
        a = self.alpha(v)
        r.trust = max(-1.0, min(1.0, (1 - a) * r.trust + a * v))
        r.interactions += 1
        if v > 0.05:
            r.positive += 1
        elif v < -0.05:
            r.negative += 1
        if note:
            r.last_note = note[:140]
        r.last_step = step
        return r.trust

    def decay(self) -> None:
        phi = 0.03 * (1 - self.persona.traits["vindictiveness"])
        for r in self.relations.values():
            r.trust *= (1 - phi)

    def ingest(self, notices: List[Notice], step: int) -> None:
        self.decay()
        for n in notices:
            self.short_term.append((n.step, n.text))
            if n.other and n.valence:
                self.update_trust(n.other, n.valence, n.step, n.text if abs(n.valence) >= 0.3 else "")
            if n.other and n.valence <= -0.5:
                self.grievances.append(Grievance(n.step, n.other, n.text[:160], n.magnitude))
                self.grievances = self.grievances[-12:]

    def record_thought(self, step: int, thought: str) -> None:
        if thought:
            self.thoughts.append((step, thought[:400]))

    def record_feedback(self, lines: List[str]) -> None:
        self.action_feedback = lines[-8:]

    def trust_of(self, other: str) -> float:
        r = self.relations.get(other)
        return r.trust if r else 0.0

    @staticmethod
    def _trust_word(t: float) -> str:
        if t <= -0.6:
            return "hated"
        if t <= -0.25:
            return "distrusted"
        if t < 0.15:
            return "neutral"
        if t < 0.5:
            return "friendly"
        return "trusted ally"

    def render(self) -> str:
        lines = ["YOUR MEMORY:", f"  long-term plan: {self.strategy}"]
        if self.thoughts:
            lines.append("  your recent private thoughts:")
            for s, t in self.thoughts:
                lines.append(f"    [step {s}] {t}")
        if self.action_feedback:
            lines.append("  results of your last actions:")
            for f in self.action_feedback:
                lines.append(f"    - {f}")
        rels = sorted(self.relations.items(), key=lambda kv: -abs(kv[1].trust))[:8]
        if rels:
            lines.append("  how you feel about others:")
            for name, r in rels:
                note = f" — {r.last_note}" if r.last_note else ""
                lines.append(f"    {name}: {self._trust_word(r.trust)} ({r.trust:+.2f}, {r.interactions} dealings){note}")
        if self.grievances:
            lines.append("  grievances you have not forgotten:")
            for g in self.grievances[-5:]:
                lines.append(f"    [step {g.step}] {g.other}: {g.what}")
        older = [t for s, t in list(self.short_term)[:-14]][-6:]
        if older:
            lines.append("  older events you remember:")
            for t in older:
                lines.append(f"    - {t[:160]}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "mood": self.mood,
            "relations": {k: {"trust": round(v.trust, 3), "interactions": v.interactions} for k, v in self.relations.items()},
            "grievances": [g.__dict__ for g in self.grievances],
            "thoughts": [{"step": s, "text": t} for s, t in self.thoughts],
        }
