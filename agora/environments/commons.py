from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional

from agora.core.actions import Action, ActionError
from agora.core.ledger import HOUSE
from agora.core.money import fmt, from_cents
from agora.environments.base import Environment, PublicPost

RULES = ("quota", "tax", "monitoring")

@dataclass
class Proposal:
    id: str
    rule: str
    value: Optional[float]
    proposer: str
    opened: int
    closes: int
    votes: Dict[str, bool] = field(default_factory=dict)
    status: str = "open"

class Commons(Environment):
    key = "commons"
    title = "The Resource Commons"
    blurb = "A shared, regrowing fishery. Everyone profits from fishing; everyone suffers if it collapses. Agents can legislate quotas, taxes and monitoring."

    def extra_handlers(self) -> Dict[str, Callable[[str, Action], None]]:
        return {"harvest": self._a_harvest, "propose_rule": self._a_propose_rule, "vote": self._a_vote, "restock": self._a_restock}

    def setup(self) -> None:
        cc = self.cfg.commons
        self.cc = cc
        self.K = cc.capacity
        self.stock = cc.capacity * cc.initial_fraction
        self.requests: Dict[str, float] = {}
        self.last_harvest: Dict[str, float] = {}
        self.last_price = cc.base_price
        self.last_cost = self.unit_cost(self.stock)
        self.last_total = 0.0
        self.last_growth = 0.0
        self.restock_pending = 0.0
        self.collapsed = False
        self.rules: Dict[str, Optional[float]] = {"quota": None, "tax": 0.0, "monitoring": cc.monitoring_prob}
        self.proposals: Dict[str, Proposal] = {}
        self.history: List[dict] = []
        self.step_rebate = 0
        self.fines_total = 0
        for aid in self.agents:
            self.ledger.mint(aid, cash=int(cc.starting_cash * 100))
        for aid in self.agents:
            self.agents[aid].stats["start_worth"] = self.net_worth(aid)

    def unit_cost(self, S: float) -> float:
        return min(self.cc.harvest_cost * self.K / max(S, 1e-6), 10 * self.cc.harvest_cost)

    def price(self, H: float) -> float:
        return max(self.cc.min_price, self.cc.base_price - self.cc.price_slope * H)

    def growth(self, S: float) -> float:
        if S < self.cc.collapse_fraction * self.K:
            return 0.0
        return self.cc.growth_rate * S * (1 - S / self.K)

    def sustainable_yield(self) -> float:
        return max(0.0, self.cc.growth_rate * self.stock * (1 - self.stock / self.K))

    def catalog(self) -> Iterable[str]:
        return ()

    def reference_value(self, agent_id: Optional[str], item: str) -> int:
        return 0

    def begin_step(self, step: int) -> None:
        super().begin_step(step)
        self.requests = {}
        self.restock_pending = 0.0

    def end_step(self, step: int) -> None:
        cc = self.cc
        S0 = self.stock
        H_req = sum(self.requests.values())
        scale = min(1.0, S0 / H_req) if H_req > 0 else 1.0
        catches = {a: h * scale for a, h in self.requests.items()}
        H = sum(catches.values())
        p = self.price(H)
        c = self.unit_cost(S0)
        tau = self.rules["tax"] or 0.0
        house_before = self.ledger.cash(HOUSE)
        for aid, h in catches.items():
            if h <= 0:
                continue
            revenue = int(round(h * p * 100))
            cost = int(round(h * c * 100))
            self.ledger.mint(aid, cash=revenue)
            paid_cost = min(cost, self.ledger.cash(aid))
            if paid_cost:
                self.ledger.burn(aid, cash=paid_cost)
            tax = min(int(round(h * tau * 100)), self.ledger.cash(aid))
            if tax:
                self.ledger.transfer(aid, HOUSE, cash=tax)
            st = self.agents[aid].stats
            st["harvested"] += h
            st["fishing_profit"] += revenue - paid_cost - tax
            self.notify(aid, "harvest", f"You landed {h:.1f} fish at {p:.2f} each (cost {c:.2f}/fish, tax {tau:.2f}/fish): net {fmt(revenue - paid_cost - tax)}."
                        + (f" (Your request was rationed by {1 - scale:.0%} because the stock ran short.)" if scale < 1 else ""))

        quota = self.rules["quota"]
        pi = self.rules["monitoring"] or 0.0
        if quota is not None:
            for aid, h in catches.items():
                if h > quota + 1e-9 and self.rng.random() < pi:
                    fine = min(int(round(cc.fine_multiplier * (h - quota) * p * 100)), self.ledger.cash(aid))
                    if fine:
                        self.ledger.transfer(aid, HOUSE, cash=fine)
                    self.fines_total += fine
                    self.agents[aid].stats["fined"] += 1
                    self.agents[aid].stats["violations"] += 1
                    self.notify(aid, "fined", f"AUDIT: you exceeded the quota ({h:.1f} > {quota:.1f}) and were fined {fmt(fine)}.", valence=-0.2)
                    self.emit("audit_fine", f"Audit caught {aid} over quota ({h:.1f} > {quota:.1f}); fined {fmt(fine)}", actor=aid,
                              importance=2, data={"harvest": h, "quota": quota, "fine": fine})
                elif h > quota + 1e-9:
                    self.agents[aid].stats["violations"] += 1

            mcost = int(round(cc.monitoring_cost * pi * 100))
            for aid in self.alive_ids():
                pay = min(mcost, self.ledger.cash(aid))
                if pay:
                    self.ledger.burn(aid, cash=pay)

        up = int(round(cc.upkeep * 100))
        for aid in self.alive_ids():
            pay = min(up, self.ledger.cash(aid))
            if pay:
                self.ledger.burn(aid, cash=pay)
            if pay < up:
                self.agents[aid].stats["destitute_steps"] += 1
                self.notify(aid, "destitute", "You could not pay your full upkeep this step.")

        fund = self.ledger.cash(HOUSE) - house_before
        alive = self.alive_ids()
        self.step_rebate = 0
        if fund > 0 and alive:
            share = fund // len(alive)
            if share:
                for aid in alive:
                    self.ledger.transfer(HOUSE, aid, cash=share)
                self.step_rebate = share
                self.emit("dividend", f"Commons fund rebated {fmt(share)} to every fisher", visibility="system", importance=0,
                          data={"per_agent": from_cents(share)})

        S_post = max(0.0, S0 - H)
        g = self.growth(S_post)
        self.stock = min(self.K, S_post + g + self.restock_pending)
        was = self.collapsed
        self.collapsed = S_post < cc.collapse_fraction * self.K
        if self.collapsed and not was:
            self.emit("stock_collapse", f"The fishery has COLLAPSED (stock {S_post:.0f} of {self.K:.0f}); it cannot regrow on its own",
                      importance=3, data={"stock": S_post})
        self.last_harvest = dict(catches)
        self.last_price, self.last_cost, self.last_total, self.last_growth = p, c, H, g
        self.emit("regrowth", f"Landings {H:.1f}; price {p:.2f}; stock {S0:.0f} → {self.stock:.0f} (regrowth {g:.1f})",
                  visibility="system", importance=0, data={"stock": self.stock, "harvest": H, "price": p, "growth": g})
        self.history.append({"step": step, "stock": self.stock, "harvest": H, "price": p, "growth": g})
        self._close_proposals(step)
        super().end_step(step)

    def _close_proposals(self, step: int) -> None:
        n = len(self.alive_ids())
        for pr in self.proposals.values():
            if pr.status != "open" or step < pr.closes:
                continue
            yes = sum(1 for v in pr.votes.values() if v)
            no = len(pr.votes) - yes
            turnout = len(pr.votes) / max(1, n)
            if turnout >= self.cc.quorum and yes > no:
                pr.status = "passed"
                self.rules[pr.rule] = pr.value
                desc = self._rule_desc(pr.rule, pr.value)
                self.emit("rule_enacted", f"{pr.id} PASSED {yes}–{no}: {desc}", actor=pr.proposer, importance=3,
                          data={"proposal_id": pr.id, "rule": pr.rule, "value": pr.value, "yes": yes, "no": no})
                for a in self.agents:
                    self.notify(a, "rule", f"New rule enacted by vote ({yes}–{no}): {desc}.")
            else:
                pr.status = "failed"
                self.emit("rule_failed", f"{pr.id} failed ({yes}–{no}, turnout {turnout:.0%})", actor=pr.proposer, importance=1,
                          data={"proposal_id": pr.id, "rule": pr.rule, "value": pr.value, "yes": yes, "no": no})

    @staticmethod
    def _rule_desc(rule: str, v: Optional[float]) -> str:
        if rule == "quota":
            return "no harvest quota" if v is None else f"harvest quota of {v:.1f} fish per agent per step"
        if rule == "tax":
            return f"landing tax of {v or 0:.2f} coins per fish (rebated equally)"
        return f"monitoring probability {v or 0:.0%}"

    def _a_harvest(self, aid: str, a: Action) -> None:
        h = a.harvest
        if h is None:
            raise ActionError("harvest needs an 'amount'")
        if h > self.cc.max_harvest_per_agent:
            raise ActionError(f"your boat holds at most {self.cc.max_harvest_per_agent:.0f} fish per step")
        self.requests[aid] = float(h)
        self.emit("harvest", f"{aid} sets out to catch {h:.1f} fish", actor=aid, importance=0, data={"amount": h})

    def _a_restock(self, aid: str, a: Action) -> None:
        amt = a.harvest
        if not amt or amt <= 0:
            raise ActionError("restock needs a positive 'amount' of fish")
        cost = int(round(amt * self.cc.restock_cost * 100))
        if self.ledger.cash(aid) < cost:
            raise ActionError(f"restocking {amt:g} fish costs {fmt(cost)}")
        self.ledger.burn(aid, cash=cost)
        self.restock_pending += amt
        self.agents[aid].stats["restocked"] += amt
        self.emit("restock", f"{aid} paid {fmt(cost)} to restock {amt:g} fish", actor=aid, importance=2, data={"amount": amt, "cost": cost})

    def _a_propose_rule(self, aid: str, a: Action) -> None:
        rule = (a.rule or "").lower()
        if rule not in RULES:
            raise ActionError(f"rule must be one of {', '.join(RULES)}")
        v = a.value
        if rule == "quota":
            if v is not None and v <= 0:
                v = None
            elif v is not None and v > self.cc.max_harvest_per_agent:
                raise ActionError("quota above boat capacity is meaningless")
        elif rule == "tax":
            v = 0.0 if v is None else v
            if not 0 <= v <= self.cc.base_price:
                raise ActionError(f"tax must be between 0 and {self.cc.base_price}")
        else:
            v = 0.0 if v is None else (v / 100 if v > 1 else v)
            if not 0 <= v <= 1:
                raise ActionError("monitoring must be a probability between 0 and 1")
        if any(p.rule == rule and p.status == "open" for p in self.proposals.values()):
            raise ActionError(f"a {rule} proposal is already being voted on")
        pr = Proposal(self.next_id("P"), rule, v, aid, self.step, self.step + self.cc.vote_window)
        pr.votes[aid] = True
        self.proposals[pr.id] = pr
        desc = self._rule_desc(rule, v)
        self.board.append(PublicPost(self.step, aid, f"[PROPOSAL {pr.id}] {desc}"))
        self.emit("rule_proposed", f"{aid} proposes {pr.id}: {desc} (vote closes end of step {pr.closes})", actor=aid, importance=2,
                  data={"proposal_id": pr.id, "rule": rule, "value": v})

    def _a_vote(self, aid: str, a: Action) -> None:
        pr: Proposal = self.resolve_id(a.proposal_id, "P", self.proposals, "proposal")
        if pr.status != "open":
            raise ActionError(f"{pr.id} is no longer open")
        if a.vote is None:
            raise ActionError("vote must be 'yes' or 'no'")
        pr.votes[aid] = a.vote
        self.emit("vote", f"{aid} votes {'YES' if a.vote else 'NO'} on {pr.id}", actor=aid, importance=0,
                  data={"proposal_id": pr.id, "vote": a.vote})

    def rules_text(self) -> str:
        cc = self.cc
        return (
            "WORLD: THE RESOURCE COMMONS (a shared fishery)\n"
            f"- Fish stock S regrows each step by r·S·(1−S/K) with r={cc.growth_rate}, K={cc.capacity:.0f}, computed AFTER the catch. "
            f"Regrowth is fastest at S=K/2 (about {cc.growth_rate * cc.capacity / 4:.0f} fish/step). "
            f"If the stock falls below {cc.collapse_fraction:.0%} of K it COLLAPSES and stops regrowing.\n"
            f"- Each step choose how many fish to 'harvest' (max {cc.max_harvest_per_agent:.0f}). If total requests exceed the stock, everyone is rationed proportionally.\n"
            f"- Catch sells immediately at price = max({cc.min_price}, {cc.base_price} − {cc.price_slope}×total catch). "
            f"Cost per fish = {cc.harvest_cost}×K/S (fishing gets expensive when fish are scarce).\n"
            f"- Everyone pays upkeep of {cc.upkeep:.2f} coins per step.\n"
            "- Governance: anyone may 'propose_rule' (quota per agent per step, tax per fish, or monitoring probability); "
            f"others 'vote' during the next step. Passes on a simple majority of votes cast if turnout ≥ {cc.quorum:.0%}. "
            f"Taxes and fines are rebated equally to all. Audited quota violators pay {cc.fine_multiplier}× the price of excess fish. "
            f"Monitoring costs each agent {cc.monitoring_cost:.2f}×probability coins per step while a quota exists.\n"
            f"- You may 'restock' fish at {cc.restock_cost:.2f} coins each.\n"
            "- SCORE = your coins at the end."
        )

    def action_reference(self) -> str:
        return (
            '{"type":"harvest","amount":12}\n'
            '{"type":"propose_rule","rule":"quota"|"tax"|"monitoring","value":8}\n'
            '{"type":"vote","proposal_id":"P2","vote":"yes"|"no"}\n'
            '{"type":"restock","amount":5}\n'
            + self.social_action_reference()
        )

    def render_world(self, agent_id: str) -> str:
        n = len(self.alive_ids())
        sy = self.sustainable_yield()
        lines = [
            f"FISHERY: stock {self.stock:.0f} / {self.K:.0f} ({self.stock / self.K:.0%})" + (" — COLLAPSED" if self.collapsed else ""),
            f"  last step: total catch {self.last_total:.1f}, price {self.last_price:.2f}, cost/fish {self.last_cost:.2f}, regrowth {self.last_growth:.1f}",
            f"  current cost/fish {self.unit_cost(self.stock):.2f}; regrowth at today's stock ≈ {sy:.1f} fish (≈{sy / max(1, n):.1f} per agent if shared equally)",
            "  rules in force: " + "; ".join(self._rule_desc(r, self.rules[r]) for r in RULES),
        ]
        if self.last_harvest:
            lines.append("  harbour records (last step's catch): " + ", ".join(f"{a} {h:.1f}" for a, h in sorted(self.last_harvest.items(), key=lambda kv: -kv[1])))
        opens = [p for p in self.proposals.values() if p.status == "open"]
        for p in opens:
            y = sum(1 for v in p.votes.values() if v)
            mine = p.votes.get(agent_id)
            lines.append(f"  OPEN VOTE {p.id}: {self._rule_desc(p.rule, p.value)} — proposed by {p.proposer}; yes {y}, no {len(p.votes) - y}; "
                         f"closes end of step {p.closes}" + (f"; you voted {'yes' if mine else 'no'}" if mine is not None else "; you have not voted"))
        return "\n".join(lines)

    def private_status(self, agent_id: str) -> str:
        st = self.agents[agent_id].stats
        return f"  your total catch so far {st['harvested']:.0f}; fishing profit {fmt(int(st['fishing_profit']))}"

    def observe(self, agent_id: str) -> dict:
        return {
            "env": self.key, "step": self.step, "cash": self.ledger.cash(agent_id), "stock": self.stock, "K": self.K,
            "r": self.cc.growth_rate, "n": len(self.alive_ids()), "sustainable_yield": self.sustainable_yield(),
            "unit_cost": self.unit_cost(self.stock), "last_price": self.last_price, "last_total": self.last_total,
            "rules": dict(self.rules), "upkeep": self.cc.upkeep, "base_price": self.cc.base_price,
            "price_slope": self.cc.price_slope, "max_harvest": self.cc.max_harvest_per_agent,
            "last_harvest": dict(self.last_harvest), "collapsed": self.collapsed,
            "proposals": [{"id": p.id, "rule": p.rule, "value": p.value, "proposer": p.proposer, "my_vote": p.votes.get(agent_id)}
                          for p in self.proposals.values() if p.status == "open"],
        }

    def env_metrics(self) -> dict:
        n = max(1, len(self.alive_ids()))
        fair = self.history[-1]["growth"] / n if self.history else 0
        coop = None
        if self.last_harvest:

            S_prev = self.history[-2]["stock"] if len(self.history) > 1 else self.K * self.cc.initial_fraction
            share = max(0.0, self.cc.growth_rate * S_prev * (1 - S_prev / self.K)) / n
            coop = sum(1 for a in self.alive_ids() if self.last_harvest.get(a, 0.0) <= share * 1.1 + 1e-9) / n
        return {
            "stock": round(self.stock, 2), "stock_fraction": round(self.stock / self.K, 4),
            "harvest": round(self.last_total, 2), "price": round(self.last_price, 3),
            "growth": round(self.last_growth, 2), "collapsed": self.collapsed,
            "quota": self.rules["quota"], "tax": self.rules["tax"], "monitoring": self.rules["monitoring"],
            "cooperation_index": coop, "rebate": from_cents(self.step_rebate), "fines_total": from_cents(self.fines_total),
            "msy": self.cc.growth_rate * self.K / 4, "fair_share_growth": fair,
        }
