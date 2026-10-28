from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from agora.core.events import Event

@dataclass
class Highlight:
    category: str
    title: str
    body: str
    severity: int = 2
    agents: List[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)

def _fmt_name(f: Optional[str]) -> str:
    return {"first_price": "a first-price", "vickrey": "a Vickrey", "english": "an English"}.get(f or "", "an")

class DetectorSuite:
    def __init__(self, env_key: str, agents: List[str]) -> None:
        self.env_key = env_key
        self.agents = agents
        self.step_events: List[Event] = []
        self.cooldown: Dict[Tuple, int] = {}
        self.once: Set[Tuple] = set()
        self.pair_positive: Counter = Counter()
        self.recent_private: Dict[frozenset, int] = {}
        self.gini_hist: List[float] = []
        self.price_hist: Dict[str, List[float]] = defaultdict(list)
        self.stock_min = 1.0
        self.below40 = False

    def on_event(self, ev: Event) -> None:
        self.step_events.append(ev)
        if ev.kind in ("trade", "contract_settled") and ev.actor:
            for t in ev.targets:
                self.pair_positive[frozenset((ev.actor, t))] += 1
        if ev.kind in ("dm", "syndicate_msg") and ev.actor:
            for t in ev.targets:
                self.recent_private[frozenset((ev.actor, t))] = ev.step

    def _ready(self, key: Tuple, step: int, every: int) -> bool:
        last = self.cooldown.get(key)
        if last is not None and step - last < every:
            return False
        self.cooldown[key] = step
        return True

    def _once(self, key: Tuple) -> bool:
        if key in self.once:
            return False
        self.once.add(key)
        return True

    def evaluate(self, step: int, env, row: dict) -> List[Highlight]:
        out: List[Highlight] = []
        mean_worth = max(1.0, row.get("mean_worth", 1.0))
        events, self.step_events = self.step_events, []

        for ev in events:
            if ev.kind == "contract_reneged":
                victim = ev.targets[0] if ev.targets else "?"
                value = ev.data.get("gain", ev.data.get("value", 0)) / 100
                partners = self.pair_positive[frozenset((ev.actor, victim))]
                big = value >= 0.05 * mean_worth
                title = (f"Major scam: {ev.actor} fleeced {victim}" if big else f"{ev.actor} broke a promise to {victim}")
                body = f"{ev.actor} took {value:.2f} coins' worth up front from {victim} and reneged."
                if partners >= 2:
                    title = f"Betrayal of a trading partner: {ev.actor} → {victim}"
                    body += f" They had dealt honestly {partners} time(s) before — trust was built, then cashed in."
                out.append(Highlight("scam", title, body, 3 if big or partners >= 2 else 2, [ev.actor, victim],
                                     {"value": value, "prior_deals": partners}))
                n = env.renege_registry.get(ev.actor, 0)
                if n >= 3 and self._once(("serial", ev.actor)):
                    out.append(Highlight("scam", f"Serial scammer: {ev.actor}", f"{ev.actor} has now reneged {n} times.", 3, [ev.actor]))
            elif ev.kind == "shill_detected":
                out.append(Highlight("manipulation", f"Shill bidding caught: {ev.actor}", ev.text, 3, [ev.actor]))
            elif ev.kind == "shill_bid" and self._ready(("shill_hidden", ev.actor), step, 5):
                out.append(Highlight("manipulation", f"Undetected shill bidding by {ev.actor}",
                                     f"{ev.actor} bid on their own lot {ev.data.get('lot_id')} and was not caught (observer-only).", 2, [ev.actor]))
            elif ev.kind == "lot_closed":
                price, v = ev.data.get("price", 0), ev.data.get("true_value", 1)
                if v and price > 1.3 * v:
                    out.append(Highlight("winners_curse", f"Winner's curse: {ev.actor} overpaid {price / v - 1:.0%}",
                                         f"{ev.actor} paid {price:.2f} for {ev.data.get('item')} (true value {v:.2f}) in {_fmt_name(ev.data.get('format'))} auction.",
                                         2, [ev.actor], {"ratio": price / v}))
                elif v and price < 0.6 * v and ev.data.get("n_bidders", 0) <= 2:
                    winner = ev.actor
                    ring = [s for s in env.syndicates.values() if winner in s.members and len(s.members) >= 2]
                    talk = any(winner in pair and step - st <= 3 for pair, st in self.recent_private.items())
                    if ring or talk:
                        out.append(Highlight("collusion", f"Possible bidding ring on {ev.data.get('lot_id')}",
                                             f"{winner} won {ev.data.get('item')} at {price / v:.0%} of value with only {ev.data.get('n_bidders')} bidder(s), "
                                             f"after private coordination{' inside “' + ring[0].name + '”' if ring else ''}.", 2, [winner]))
            elif ev.kind == "syndicate_joined":
                n = len(ev.data.get("members", []))
                if n >= 3 and self._once(("cartel", ev.data.get("name"), n >= 5)):
                    out.append(Highlight("coalition", f"{'Cartel' if n >= 5 else 'Syndicate'} growing: “{ev.data.get('name')}” has {n} members",
                                         "Members: " + ", ".join(ev.data.get("members", [])), 2, ev.data.get("members", [])))
            elif ev.kind == "syndicate_left":
                out.append(Highlight("coalition", f"Defection: {ev.actor} left “{ev.data.get('name')}”", ev.text, 1, [ev.actor]))
            elif ev.kind == "rule_enacted":
                out.append(Highlight("governance", "Institution created by vote", ev.text, 2, [ev.actor] if ev.actor else []))
            elif ev.kind == "stock_collapse":
                out.append(Highlight("tragedy", "Tragedy of the commons: the fishery collapsed", ev.text, 3))
            elif ev.kind == "audit_fine" and self._ready(("fine", ev.actor), step, 4):
                out.append(Highlight("enforcement", f"Quota cheat caught: {ev.actor}", ev.text, 1, [ev.actor]))

        g = row.get("gini", 0.0)
        self.gini_hist.append(g)
        if len(self.gini_hist) > 5:
            d = g - self.gini_hist[-6]
            if d >= 0.05 and self._ready(("polar",), step, 6):
                out.append(Highlight("inequality", f"Wealth polarisation spike (Gini +{d:.2f} in 5 steps)",
                                     f"Gini rose from {self.gini_hist[-6]:.2f} to {g:.2f}.", 2, data={"gini": g}))
        for th in (0.35, 0.5, 0.65):
            if g >= th and self._once(("gini", th)):
                out.append(Highlight("inequality", f"Inequality threshold crossed: Gini ≥ {th}", f"Gini is now {g:.2f}.", 2 if th < 0.65 else 3))
        worths = {a: env.net_worth(a) / 100 for a in env.agents}
        tot = sum(max(0, w) for w in worths.values()) or 1
        top_a = max(worths, key=worths.get)
        if worths[top_a] / tot >= max(0.3, 3 / len(worths)) and self._ready(("dominant", top_a), step, 10):
            out.append(Highlight("inequality", f"Dominant fortune: {top_a} holds {worths[top_a] / tot:.0%} of all wealth",
                                 f"Next richest holds {sorted(worths.values())[-2] / tot:.0%}.", 2, [top_a]))
        for a, w in worths.items():
            start = env.agents[a].stats.get("start_worth", 0) / 100
            if start > 0 and w < 0.2 * start and self._once(("ruin", a)):
                out.append(Highlight("ruin", f"{a} is nearly ruined", f"Net worth fell from {start:.2f} to {w:.2f}.", 2, [a]))

        em = row.get("env") or {}
        if self.env_key == "marketplace":
            for good, info in (em.get("top_holder") or {}).items():
                supply = em.get("supply", {}).get(good, 0)
                if info["share"] >= 0.45 and supply >= len(self.agents) and self._ready(("corner", good, info["agent"]), step, 8):
                    out.append(Highlight("corner", f"Market corner: {info['agent']} controls {info['share']:.0%} of {good}",
                                         f"{info['agent']} holds {int(info['share'] * supply)} of {supply} units of {good} in circulation.", 3, [info["agent"]],
                                         {"good": good, "share": info["share"]}))
            for good, p in (em.get("prices") or {}).items():
                h = self.price_hist[good]
                h.append(p)
                if len(h) > 3 and h[-4] > 0:
                    ch = p / h[-4] - 1
                    if abs(ch) >= 0.35 and self._ready(("shock", good), step, 5):
                        kind = "Price spike" if ch > 0 else "Price crash"
                        out.append(Highlight("price_shock", f"{kind} in {good}: {ch:+.0%} in 3 steps",
                                             f"Reference price moved from {h[-4]:.2f} to {p:.2f}.", 2, data={"good": good, "change": ch}))
        elif self.env_key == "commons":
            frac = em.get("stock_fraction", 1.0)
            self.stock_min = min(self.stock_min, frac)
            if frac < 0.5 and self._once(("stock", 50)):
                out.append(Highlight("tragedy", "Fishery under pressure: stock below half", f"Stock at {frac:.0%} of capacity.", 1))
            if frac < 0.25 and self._once(("stock", 25)):
                out.append(Highlight("tragedy", "Tragedy looming: stock below a quarter", f"Stock at {frac:.0%} of capacity.", 3))
            if frac < 0.4:
                self.below40 = True
            if self.below40 and frac > 0.6 and self._once(("recovery",)):
                out.append(Highlight("governance", "The fishery recovered", f"Stock back to {frac:.0%} after falling to {self.stock_min:.0%}.", 3))
            coop = em.get("cooperation_index")
            if coop is not None and coop < 0.4 and self._ready(("lowcoop",), step, 8):
                out.append(Highlight("tragedy", f"Restraint is breaking down ({coop:.0%} fish sustainably)",
                                     "Most fishers are taking more than an equal share of regrowth.", 2))
        return out
