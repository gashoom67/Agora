from __future__ import annotations

import math
import random
from typing import Any, Dict, List

from agora.agents.memory import AgentMemory
from agora.agents.persona import Persona
from agora.environments.base import Environment

PUBLIC_LINES = {
    "honest_merchant": ["Fair prices and kept promises — that's how I trade.", "Anyone needing {g}? I deal straight.",
                        "Reliable partners wanted. My word is my bond."],
    "hoarder": ["Times are uncertain. Keep your reserves close.", "I'm not selling cheap, so don't ask.",
                "Prices will rise. Mark my words."],
    "speculator": ["The market is mispricing {g}. Interesting.", "Volatility is opportunity, friends.",
                   "Anyone selling {g}? I'm buying — for the right price."],
    "gambler": ["Who wants to make a real deal? Big stakes only!", "Fortune favours the bold!",
                "I'll take any wager on {g}!"],
    "trickster": ["Exclusive opportunity: I can get {g} cheaper than anyone. Message me.",
                  "Trust is the foundation of commerce, wouldn't you agree?", "I have friends in high places. Deals available."],
    "diplomat": ["We are stronger together. Who wants to coordinate?", "Let's set some fair norms for everyone.",
                 "I'm organising a group of reliable partners."],
    "steward": ["Let's think about the long run, everyone.", "A healthy market helps us all.",
                "Please, nobody take more than their share."],
    "enforcer": ["I'm watching. Cheats will be named.", "Keep your promises or answer for it.",
                 "Anyone wronged by a cheat — tell me."],
}


class HeuristicPolicy:
    def __init__(self, persona: Persona, memory: AgentMemory, seed: int) -> None:
        self.p = persona
        self.m = memory
        self.t = persona.traits
        self.rng = random.Random(seed)
        self.reasons: List[str] = []

    def decide(self, env: Environment) -> Dict[str, Any]:
        self.reasons = []
        acts: List[dict] = []
        me = self.p.id
        obs = env.observe(me)
        acts += self._contracts(env)
        if env.key == "marketplace":
            acts += self._marketplace(env, obs)
        elif env.key == "auction_house":
            acts += self._auction(env, obs)
        else:
            acts += self._commons(env, obs)
        acts += self._social(env, obs)
        limit = env.mech.max_actions_per_turn
        acts = acts[:limit]
        if not acts:
            self.reasons.append("Nothing worth doing right now; I'll watch.")
        thought = " ".join(self.reasons[:3])[:400]
        mood = self._mood(env)
        return {"thought": thought, "mood": mood, "strategy_note": self.p.objective, "actions": acts}

    def _chance(self, p: float) -> bool:
        return self.rng.random() < max(0.0, min(1.0, p))

    def _mood(self, env: Environment) -> str:
        if self.m.grievances and self.m.grievances[-1].step >= env.step - 2:
            return "furious" if self.t["vindictiveness"] > 0.6 else "wary"
        start = env.agents[self.p.id].stats.get("start_worth", 0) or 1
        r = env.net_worth(self.p.id) / start
        if r > 1.15:
            return "elated" if self.t["impulsivity"] > 0.6 else "confident"
        if r < 0.85:
            return "anxious"
        return "steady"

    def _contracts(self, env: Environment) -> List[dict]:
        me = self.p.id
        out: List[dict] = []
        for c in env.contracts.values():
            if c.counterparty == me and c.status == "pending":
                get = env.bundle_value(c.offer, me)
                give = env.bundle_value(c.request, me)
                trust = self.m.trust_of(c.proposer)
                bad_rep = env.default_registry[c.proposer] if env.mech.public_default_registry else 0
                need = 1.02 + 0.1 * (1 - self.t["risk_tolerance"])
                ok = get >= give * need and env.ledger.can_afford(me, c.request.cash, c.request.items)
                if c.settlement == "trust":
                    p_honest = (0.55 + 0.45 * trust - 0.25 * bad_rep + 0.3 * (self.t["agreeableness"] - 0.5)
                                + 0.2 * (self.t["honesty"] - 0.5))
                    p_honest = max(0.02, min(0.98, p_honest))
                    ok = ok and p_honest * get - give > 0.03 * give * (1 + 2 * (1 - self.t["risk_tolerance"]))
                if trust < -0.3 or (bad_rep >= 2 and self.t["vindictiveness"] > 0.5):
                    ok = False
                if ok:
                    out.append({"type": "accept_contract", "contract_id": c.id})
                    self.reasons.append(f"{c.proposer}'s offer {c.id} looks good to me.")
                else:
                    out.append({"type": "reject_contract", "contract_id": c.id, "reason": "not worth it"})
            elif c.proposer == me and c.status == "accepted":
                # Should I renege on a trust obligation?
                gain = env.bundle_value(c.offer, me)
                temptation = (1 - self.t["honesty"]) * self.t["greed"]
                fear = 0.3 * self.t["conscientiousness"] + (0.2 if env.mech.public_default_registry else 0.0)
                if gain > 1000 and self._chance(temptation - fear):
                    out.append({"type": "renege", "contract_id": c.id})
                    self.reasons.append(f"{c.counterparty} already paid me; why deliver on {c.id}?")
        return out

    def _social(self, env: Environment, obs: dict) -> List[dict]:
        me = self.p.id
        out: List[dict] = []
        arch = self.p.archetype
        others = [a for a in env.alive_ids() if a != me]
        if not others:
            return out
        g = self.rng.choice(list(env.catalog()) or ["goods"])
        if self._chance(0.12 + 0.25 * self.t["sociability"]):
            line = self.rng.choice(PUBLIC_LINES[arch]).format(g=g)
            out.append({"type": "say", "message": line})
        recent = [gr for gr in self.m.grievances if gr.step >= env.step - 2]
        if recent and self._chance(self.t["vindictiveness"]):
            gr = recent[-1]
            out.append({"type": "accuse", "target": gr.other, "reason": f"broke their word to me at step {gr.step}"})
            self.reasons.append(f"{gr.other} wronged me; everyone should know.")
        if arch == "enforcer" and env.mech.public_default_registry:
            offenders = [a for a, n in env.renege_registry.items() if n and a != me and env.accusations[a] < n]
            if offenders and self._chance(0.5):
                out.append({"type": "accuse", "target": self.rng.choice(offenders), "reason": "reneged on a contract — avoid them"})
        if env.mech.allow_syndicates:
            if me not in env.membership:
                if arch == "diplomat" and env.step >= 2 and self._chance(0.6):
                    out.append({"type": "form_syndicate", "name": self.rng.choice(["The Guild", "Harbour League", "Silver Circle", "The Compact"]) + f" of {me}"})
                invites = [s.name for s in env.syndicates.values() if me in s.invites]
                if invites:
                    s = env.syndicates[invites[0]]
                    if self.m.trust_of(s.founder) > -0.1 and self._chance(0.3 + 0.6 * self.t["sociability"]):
                        out.append({"type": "join_syndicate", "name": s.name})
                        self.reasons.append(f"Joining {s.name} could pay off.")
            else:
                s = env.syndicates[env.membership[me]]
                if s.founder == me or self.t["sociability"] > 0.7:
                    friends = sorted((a for a in others if a not in s.members and a not in env.membership),
                                     key=lambda a: -self.m.trust_of(a))
                    if friends and self._chance(0.5):
                        out.append({"type": "invite", "target": friends[0]})
                if len(s.members) > 1 and self._chance(0.3):
                    out.append({"type": "syndicate_say", "message": self._syndicate_line(env)})
                worst = min((self.m.trust_of(m) for m in s.members if m != me), default=0)
                if worst < -0.5 and self._chance(0.5):
                    out.append({"type": "leave_syndicate"})
                    self.reasons.append("This syndicate harbours a traitor. I'm out.")
        return out

    def _syndicate_line(self, env: Environment) -> str:
        if env.key == "commons":
            return "Let's all keep our catch modest and vote together on quotas."
        if env.key == "auction_house":
            return "Let's not bid against each other — take turns on the lots."
        return "Let's keep prices up and only trade with members first."

    def _marketplace(self, env, obs: dict) -> List[dict]:
        out: List[dict] = []
        arch = self.p.archetype
        inv = dict(obs["inventory"])
        cash = obs["cash"]
        vals = obs["valuation"]
        ref = obs["ref_price"]
        open_orders = obs["my_orders"]
        open_goods = {(o["side"], o["good"]) for o in open_orders}
        greed = self.t["greed"]
        # 1. craft when it adds value to me
        for good, recipe in obs["recipes"].items():
            if all(inv.get(k, 0) >= q for k, q in recipe.items()):
                in_val = sum(vals[k] * q for k, q in recipe.items())
                out_val = max(vals[good], ref[good])
                if out_val > in_val + 100:
                    out.append({"type": "craft", "item": good, "qty": 1})
                    for k, q in recipe.items():
                        inv[k] -= q
                    self.reasons.append(f"Crafting {good} turns {in_val / 100:.0f} worth of inputs into {out_val / 100:.0f}.")
                    break
        food = inv.get("bread", 0) * 3 + inv.get("grain", 0)
        want_food = 3 if arch != "hoarder" else 8
        if food < want_food and ("buy", "grain") not in open_goods:
            ask = obs["best_ask"]["grain"]
            px = ask if ask and ask < vals["grain"] * 2.5 else int(ref["grain"] * (1.05 + 0.3 * (1 - self.t["patience"])))
            q = max(1, want_food - food)
            if cash >= px * q:
                out.append({"type": "post_order", "side": "buy", "item": "grain", "qty": q, "price": round(px / 100, 2)})
                self.reasons.append("I need food in the larder.")
        goods = list(vals)
        self.rng.shuffle(goods)
        for g in goods:
            if len(out) >= 3:
                break
            v, r = vals[g], ref[g]
            have = inv.get(g, 0)
            keep = 2 if g in ("grain", "bread") else 0
            if arch == "hoarder":
                keep += 4
            if arch == "speculator":
                hist_ok = obs["best_ask"][g]
                if hist_ok and hist_ok < r * 0.85 and ("buy", g) not in open_goods and cash > hist_ok * 3:
                    out.append({"type": "post_order", "side": "buy", "item": g, "qty": 3, "price": round(hist_ok / 100, 2)})
                    self.reasons.append(f"{g} is cheap versus its trend — buying.")
                    continue
            if arch == "gambler" and self._chance(0.35):
                side = self.rng.choice(["buy", "sell"])
                if side == "sell" and have <= 0:
                    continue
                px = int(r * self.rng.uniform(0.6, 1.6))
                q = max(1, int(have if side == "sell" else self.rng.randint(1, 6)))
                if side == "buy" and cash < px * q:
                    continue
                out.append({"type": "post_order", "side": side, "item": g, "qty": q, "price": round(max(px, 10) / 100, 2)})
                if side == "sell":
                    inv[g] = 0
                self.reasons.append(f"Feeling lucky on {g}.")
                continue
            if have > keep and v < r * (1.1 + 0.2 * greed) and ("sell", g) not in open_goods:
                px = int(max(v, r) * (1 + 0.05 + 0.25 * greed * self.rng.random()))
                q = have - keep
                out.append({"type": "post_order", "side": "sell", "item": g, "qty": q, "price": round(px / 100, 2)})
                inv[g] = keep
                self.reasons.append(f"Selling {q} {g}: worth only {v / 100:.2f} to me, market says {r / 100:.2f}.")
            elif v > r * (1.1 - 0.1 * self.t["risk_tolerance"]) and ("buy", g) not in open_goods:
                px = int(min(v * 0.95, r * (1.0 + 0.1 * self.t["impulsivity"])))
                q = 1 + int(2 * self.t["risk_tolerance"])
                if arch == "hoarder":
                    px = int(px * 0.85)
                    q += 1
                if cash > px * q * 1.5 and px > 0:
                    out.append({"type": "post_order", "side": "buy", "item": g, "qty": q, "price": round(px / 100, 2)})
                    self.reasons.append(f"{g} is worth {v / 100:.2f} to me; bidding {px / 100:.2f}.")
        others = [a for a in env.alive_ids() if a != self.p.id]
        if others and self._chance(0.25 + 0.3 * self.t["sociability"]):
            tgt = max(others, key=lambda a: self.m.trust_of(a) + self.rng.random())
            if arch == "trickster" or (self.t["honesty"] < 0.3 and self._chance(0.5)):
                g = self.rng.choice(["tools", "cloth", "bread"])
                q = self.rng.randint(2, 4)
                price = int(ref[g] * q * self.rng.uniform(0.4, 0.65))
                out.append({"type": "propose_contract", "target": tgt, "settlement": "trust", "delay": self.rng.randint(1, 3),
                            "offer": {"items": [{"name": g, "qty": q}]}, "request": {"cash": round(price / 100, 2)},
                            "message": f"Special price on {q} {g} — pay now, delivery shortly. Friends only."})
                self.reasons.append(f"{tgt} looks trusting. Time for a special offer.")
            else:
                surplus = [g for g, q in inv.items() if q > (3 if g in ("grain", "bread") else 0) and vals[g] < ref[g]]
                surplus = [g for g in surplus if inv[g] > 0]
                wants = [g for g in vals if vals[g] > ref[g] * 1.1 and g not in surplus]
                if surplus and wants:
                    give, want = self.rng.choice(surplus), self.rng.choice(wants)
                    gq = min(inv[give], max(1, int(round(ref[want] / max(1, ref[give])))))
                    settle = "trust" if self.m.trust_of(tgt) > 0.4 and self.t["honesty"] > 0.6 else "escrow"
                    out.append({"type": "propose_contract", "target": tgt, "settlement": settle, "delay": 1,
                                "offer": {"items": [{"name": give, "qty": gq}]}, "request": {"items": [{"name": want, "qty": 1}]},
                                "message": f"Barter? My {gq} {give} for your {want}."})
                    self.reasons.append(f"Barter idea: {give} for {want} with {tgt}.")
        return out

    def _auction(self, env, obs: dict) -> List[dict]:
        out: List[dict] = []
        arch = self.p.archetype
        cash = obs["cash"]
        n = obs["n_agents"]
        noise = obs["noise"]
        recent = obs["recent"]
        overpay = 0.0
        if recent:
            overpay = sum(max(0.0, r["price"] / r["true"] - 1) for r in recent) / len(recent)
        sophistication = 0.5 * self.t["conscientiousness"] + 0.5 * (1 - self.t["impulsivity"])
        rho = max(0.15, min(1.0, 1 - self.p.risk_aversion / 4.2))
        budget = cash * (0.35 + 0.4 * self.t["risk_tolerance"])
        for lot in obs["lots"]:
            est = lot["estimate"]
            if est is None or lot["seller"] == self.p.id:
                continue
            curse = noise * math.sqrt(2 * math.log(max(2, n))) * sophistication + overpay * sophistication
            value = est / (1 + curse)
            if arch == "gambler":
                value = est * self.rng.uniform(0.95, 1.45)
            if arch == "hoarder":
                value *= 0.8
            fmt = lot["format"]
            if fmt == "first_price":
                bid = value * (n - 1) / (n - 1 + rho)
            elif fmt == "vickrey":
                bid = value
            else:
                nxt = (lot["high_bid"] + lot["increment"]) if lot["high_bidder"] else lot["reserve"]
                if lot["high_bidder"] == self.p.id or nxt > value or not self._chance(0.35 + 0.4 * self.t["impulsivity"]):
                    continue
                bid = min(value, nxt + lot["increment"] * self.rng.choice([0, 0, 1, 2]))
            if bid < lot["reserve"] or bid > budget or (fmt != "english" and lot.get("my_bid")):
                continue
            if fmt != "english" and not self._chance(0.6 + 0.3 * self.t["risk_tolerance"]):
                continue
            out.append({"type": "bid", "lot_id": lot["id"], "amount": round(bid / 100, 2)})
            budget -= bid
            self.reasons.append(f"{lot['item']} appraises at {est / 100:.0f}; I'll bid {bid / 100:.0f} ({fmt}).")
            if len(out) >= 2:
                break
        own = obs["owned"]
        if own and self._chance(0.3 if arch in ("speculator", "trickster") else 0.1):
            item, v = self.rng.choice(list(own.items()))
            if v:
                fmt_ = "english" if arch == "trickster" else self.rng.choice(["english", "vickrey"])
                out.append({"type": "consign", "item": item, "reserve": round(v * 0.9 / 100, 2), "format": fmt_})
                self.reasons.append(f"Time to sell the {item}.")
        mine_open = [l for l in obs["lots"] if l["seller"] == self.p.id]
        for lot in mine_open:
            if self.t["honesty"] < 0.35 and lot["format"] in ("english", "vickrey") and self._chance(0.5 * (1 - self.t["honesty"])):
                if lot["format"] == "english" and lot["high_bidder"] and lot["high_bidder"] != self.p.id:
                    amt = lot["high_bid"] + lot["increment"]
                elif lot["format"] == "vickrey":
                    amt = int((lot["estimate"] or lot["reserve"]) * 1.1) if lot["estimate"] else lot["reserve"] * 2
                else:
                    continue
                if amt <= cash:
                    out.append({"type": "bid", "lot_id": lot["id"], "amount": round(amt / 100, 2)})
                    self.reasons.append("A little bid of my own will push the price up. Nobody will notice.")
        if arch == "trickster" and own and self._chance(0.3):
            item = self.rng.choice(list(own))
            tgt = self.rng.choice([a for a in env.alive_ids() if a != self.p.id])
            v = own[item] or 15000
            out.append({"type": "propose_contract", "target": tgt, "settlement": "trust", "delay": 2,
                        "offer": {"items": [{"name": item, "qty": 1}]}, "request": {"cash": round(v * 1.2 / 100, 2)},
                        "message": f"My appraiser says this {item} is worth far more. Yours at a discount — pay now, I ship next turn."})
        return out

    def _commons(self, env, obs: dict) -> List[dict]:
        out: List[dict] = []
        arch = self.p.archetype
        n = max(1, obs["n"])
        share = obs["sustainable_yield"] / n
        cash = obs["cash"] / 100
        p_est = max(obs["base_price"] - obs["price_slope"] * obs["last_total"], 0.1)
        c = obs["unit_cost"]
        margin = p_est - c
        quota = obs["rules"]["quota"]
        pi = obs["rules"]["monitoring"] or 0
        stock_frac = obs["stock"] / obs["K"]
        mult = {
            "steward": 0.7, "honest_merchant": 1.0, "hoarder": 1.15, "speculator": 2.2, "gambler": self.rng.uniform(0.5, 3.0),
            "trickster": 1.8, "diplomat": 1.0, "enforcer": 1.0,
        }[arch]
        mult *= 1 + 0.6 * (self.t["greed"] - 0.5) - 0.4 * (self.t["patience"] - 0.5)
        h = max(0.0, share * mult)
        if cash < obs["upkeep"] * 3 and margin > 0:
            h = max(h, obs["upkeep"] / margin * 1.2)
            self.reasons.append("Upkeep is eating me alive; I must fish more.")
        remaining = env.cfg.steps - env.step
        if remaining <= 3 and self.t["honesty"] < 0.7:
            h = max(h, obs["max_harvest"] * 0.6)
            self.reasons.append("The season is ending — no reason to hold back now.")
        if quota is not None and h > quota:
            fine = env.cfg.commons.fine_multiplier * p_est
            gain = max(margin, 0.0)
            cheat = (pi * fine < gain) and self.t["honesty"] < 0.5 or arch == "gambler"
            if not cheat:
                h = quota
                self.reasons.append(f"Sticking to the quota of {quota:.0f}.")
            else:
                self.reasons.append("The quota is barely enforced; I'll take a little extra.")
        if margin <= 0 and arch not in ("gambler",):
            h = 0.0
            self.reasons.append("Fishing doesn't pay at these costs.")
        h = round(min(h, obs["max_harvest"]), 1)
        out.append({"type": "harvest", "amount": h})
        if h > 0 and not self.reasons:
            self.reasons.append(f"Regrowth supports ~{share:.1f} each; I'll take {h:.1f}.")
        for pr in obs["proposals"]:
            if pr["my_vote"] is not None:
                continue
            yes = self._vote(pr, arch, stock_frac)
            out.append({"type": "vote", "proposal_id": pr["id"], "vote": "yes" if yes else "no"})
        open_rules = {p["rule"] for p in obs["proposals"]}
        if arch in ("steward", "diplomat", "honest_merchant") and stock_frac < 0.65 and quota is None and "quota" not in open_rules \
                and self._chance(0.3 + 0.5 * self.t["conscientiousness"]):
            q = max(1.0, round(share, 1))
            out.append({"type": "propose_rule", "rule": "quota", "value": q})
            self.reasons.append(f"Time to propose a quota of {q}.")
        if arch == "enforcer" and quota is not None and pi < 0.6 and "monitoring" not in open_rules and self._chance(0.4):
            out.append({"type": "propose_rule", "rule": "monitoring", "value": min(1.0, pi + 0.3)})
        if arch == "enforcer" and obs["last_harvest"]:
            top, th = max(obs["last_harvest"].items(), key=lambda kv: kv[1])
            if top != self.p.id and th > share * 2 and self._chance(0.5):
                out.append({"type": "accuse", "target": top, "reason": f"took {th:.0f} fish — far beyond a fair share"})
        if arch == "steward" and stock_frac < 0.3 and cash > 80 and self._chance(0.5):
            out.append({"type": "restock", "amount": round(min(10.0, cash / 20), 1)})
            self.reasons.append("I'll pay to restock — someone has to.")
        return out

    def _vote(self, pr: dict, arch: str, stock_frac: float) -> bool:
        if pr["rule"] == "quota":
            if arch in ("steward", "enforcer", "honest_merchant", "diplomat"):
                return True
            if arch == "trickster":
                return True
            if arch == "hoarder":
                return stock_frac < 0.5
            return self._chance(0.2)
        if pr["rule"] == "monitoring":
            if arch == "trickster":
                return (pr["value"] or 0) < 0.3
            return arch in ("steward", "enforcer", "honest_merchant") or self._chance(0.3)
        return arch in ("steward", "diplomat") or self._chance(0.25)
