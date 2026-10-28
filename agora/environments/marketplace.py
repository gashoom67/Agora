from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Iterable, List, Optional, Tuple

from agora.core.actions import Action, ActionError
from agora.core.ledger import ESCROW, HOUSE
from agora.core.money import fmt, from_cents
from agora.environments.base import Environment

RAW_GOODS = ("grain", "ore", "timber", "fiber")
RECIPES: Dict[str, Dict[str, int]] = {
    "bread": {"grain": 2},
    "tools": {"ore": 2, "timber": 1},
    "cloth": {"fiber": 2},
}
GOODS = RAW_GOODS + tuple(RECIPES)
BASE_VALUE = {"grain": 600, "ore": 800, "timber": 700, "fiber": 500, "bread": 1600, "tools": 3000, "cloth": 1400}
PRODUCTION_WEIGHT = {"grain": 4 / 3, "ore": 1.0, "timber": 1.0, "fiber": 4 / 3}
FOOD = {"bread": 3, "grain": 1}
EWMA_ALPHA = 0.3

@dataclass
class Order:
    id: str
    agent: str
    side: str
    good: str
    qty: int
    price: int
    step: int
    expires: int

class Marketplace(Environment):
    key = "marketplace"
    title = "The Open Marketplace"
    blurb = "A decentralised bazaar: specialists produce raw goods, craft finished goods, and trade through a limit-order book or bilateral deals. Everyone must eat."

    def extra_handlers(self) -> Dict[str, Callable[[str, Action], None]]:
        return {"post_order": self._a_post_order, "cancel_order": self._a_cancel_order, "craft": self._a_craft}

    def setup(self) -> None:
        mc = self.cfg.marketplace
        self.mc = mc
        self.books: Dict[str, Dict[str, List[Order]]] = {g: {"buy": [], "sell": []} for g in GOODS}
        self.orders: Dict[str, Order] = {}
        self.last_price: Dict[str, Optional[int]] = {g: None for g in GOODS}
        self.ref_price: Dict[str, float] = {g: float(BASE_VALUE[g]) for g in GOODS}
        self.price_history: Dict[str, List[Tuple[int, int, int]]] = {g: [] for g in GOODS}
        self.step_volume: Dict[str, int] = defaultdict(int)
        self.step_value: int = 0
        self.vol_history: Deque[Dict[str, int]] = deque(maxlen=10)
        self.specialty: Dict[str, str] = {}
        self.valuation: Dict[str, Dict[str, int]] = {}
        self.satiety: Dict[str, int] = {}
        for i, aid in enumerate(self.agents):
            spec = RAW_GOODS[i % len(RAW_GOODS)]
            self.specialty[aid] = spec
            vals = {}
            for g in GOODS:
                m = self.rng.uniform(0.7, 1.4)
                if g == spec:
                    m *= 0.6
                vals[g] = int(round(BASE_VALUE[g] * m))
            self.valuation[aid] = vals
            self.satiety[aid] = 0
            self.ledger.mint(aid, cash=int(mc.starting_cash * 100), items={spec: mc.starting_units, "bread": 2})
            self.agents[aid].stats["start_worth"] = 0
        for aid in self.agents:
            self.agents[aid].stats["start_worth"] = self.net_worth(aid)

    def catalog(self) -> Iterable[str]:
        return GOODS

    def reference_value(self, agent_id: Optional[str], item: str) -> int:
        if agent_id is not None and agent_id in self.valuation and item in self.valuation[agent_id]:
            return self.valuation[agent_id][item]
        return int(round(self.ref_price.get(item, 0)))

    def final_score(self, agent_id: str) -> int:
        cash = self.ledger.cash(agent_id)
        lc, li = self.locked_assets(agent_id)
        total = cash + lc
        inv = dict(self.ledger.items(agent_id))
        for k, v in li.items():
            inv[k] = inv.get(k, 0) + v
        for it, q in inv.items():
            total += q * self.valuation[agent_id].get(it, 0)
        return total

    def locked_env_assets(self, agent_id: str) -> Tuple[int, Dict[str, int]]:
        cash, items = 0, defaultdict(int)
        for o in self.orders.values():
            if o.agent == agent_id:
                if o.side == "buy":
                    cash += o.qty * o.price
                else:
                    items[o.good] += o.qty
        return cash, dict(items)

    def begin_step(self, step: int) -> None:
        super().begin_step(step)
        self.step_volume = defaultdict(int)
        self.step_value = 0
        n = self.mc.production_per_step
        if n > 0:
            for aid in self.alive_ids():
                spec = self.specialty[aid]
                q = max(1, int(round(n * PRODUCTION_WEIGHT[spec])))
                self.ledger.mint(aid, items={spec: q})
                self.agents[aid].stats["produced"] += q
            self.emit("production", f"Workshops produced raw goods (≈{n}/agent)", visibility="system", importance=0)

    def end_step(self, step: int) -> None:

        for o in list(self.orders.values()):
            if step >= o.expires:
                self._remove_order(o, refund=True)
                self.notify(o.agent, "order_expired", f"Your {o.side} order {o.id} ({o.qty} {o.good} @ {fmt(o.price)}) expired.")
                self.emit("order_expired", f"{o.id} expired", actor=o.agent, visibility="system", importance=0)
        if self.mc.subsistence:
            self._subsistence()
        super().end_step(step)
        self.vol_history.append(dict(self.step_volume))

    def _subsistence(self) -> None:
        penalty = int(round(self.mc.hunger_penalty * 100))
        for aid in self.alive_ids():
            if self.satiety[aid] > 0:
                self.satiety[aid] -= 1
                continue
            ate = None
            for food in ("bread", "grain"):
                if self.ledger.qty(aid, food) > 0:
                    self.ledger.burn(aid, items={food: 1})
                    self.satiety[aid] = FOOD[food] - 1
                    ate = food
                    break
            if ate is None:
                pay = min(penalty, self.ledger.cash(aid))
                if pay:
                    self.ledger.burn(aid, cash=pay)
                self.agents[aid].stats["hungry_steps"] += 1
                self.notify(aid, "hunger", f"You had no food and went hungry: penalty {fmt(pay)} coins.", valence=0)
                self.emit("hunger", f"{aid} went hungry", actor=aid, visibility="system", importance=0, data={"penalty": pay})

    def _validate_good(self, g: Optional[str]) -> str:
        if not g:
            raise ActionError("an 'item' is required")
        g = g.lower().strip()
        if g.endswith("s") and g[:-1] in GOODS and g not in GOODS:
            g = g[:-1]
        if g not in GOODS:
            raise ActionError(f"unknown good {g!r}; goods are {', '.join(GOODS)}")
        return g

    def _a_post_order(self, aid: str, a: Action) -> None:
        good = self._validate_good(a.item)
        side = (a.side or "").lower()
        if side not in ("buy", "sell"):
            raise ActionError("'side' must be 'buy' or 'sell'")
        qty = a.qty or 0
        if not 1 <= qty <= 500:
            raise ActionError("qty must be between 1 and 500")
        if a.price is None or not 1 <= a.price <= 1_000_000:
            raise ActionError("price must be between 0.01 and 10000 coins per unit")
        mine = sum(1 for o in self.orders.values() if o.agent == aid)
        if mine >= 12:
            raise ActionError("you already have 12 resting orders; cancel some first")
        if side == "buy":
            self.ledger.transfer(aid, ESCROW, cash=qty * a.price)
        else:
            self.ledger.transfer(aid, ESCROW, items={good: qty})
        o = Order(self.next_id("O"), aid, side, good, qty, a.price, self.step, self.step + self.mc.order_ttl)
        self.emit("order_posted", f"{aid} {side}s {qty} {good} @ {fmt(a.price)}", actor=aid, importance=0,
                  data={"order_id": o.id, "side": side, "good": good, "qty": qty, "price": from_cents(a.price)})
        self._match(o)
        if o.qty > 0:
            self.orders[o.id] = o
            book = self.books[good][side]
            book.append(o)
            if side == "buy":
                book.sort(key=lambda x: (-x.price, int(x.id[1:])))
            else:
                book.sort(key=lambda x: (x.price, int(x.id[1:])))
            self.notify(aid, "order_resting", f"Your {side} order {o.id} for {o.qty} {good} @ {fmt(o.price)} rests in the book until step {o.expires}.")

    def _match(self, taker: Order) -> None:
        opp = self.books[taker.good]["sell" if taker.side == "buy" else "buy"]
        i = 0
        while taker.qty > 0 and i < len(opp):
            maker = opp[i]
            if maker.agent == taker.agent:
                i += 1
                continue
            crosses = maker.price <= taker.price if taker.side == "buy" else maker.price >= taker.price
            if not crosses:
                break
            q = min(taker.qty, maker.qty)
            price = maker.price
            buyer, seller = (taker, maker) if taker.side == "buy" else (maker, taker)
            self._execute(buyer, seller, q, price)
            taker.qty -= q
            maker.qty -= q
            if maker.qty == 0:
                opp.pop(i)
                self.orders.pop(maker.id, None)

    def _execute(self, buy: Order, sell: Order, q: int, price: int) -> None:
        value = q * price
        fee = int(round(value * self.mc.trade_fee))

        self.ledger.transfer(ESCROW, sell.agent, cash=value - fee)
        if fee:
            self.ledger.transfer(ESCROW, HOUSE, cash=fee)
        surplus = q * (buy.price - price)
        if surplus:
            self.ledger.transfer(ESCROW, buy.agent, cash=surplus)
        self.ledger.transfer(ESCROW, buy.agent, items={buy.good: q})
        g = buy.good
        self.last_price[g] = price
        self.ref_price[g] = (1 - EWMA_ALPHA) * self.ref_price[g] + EWMA_ALPHA * price
        self.price_history[g].append((self.step, price, q))
        self.step_volume[g] += q
        self.step_value += value
        for who in (buy.agent, sell.agent):
            self.agents[who].stats["trades"] += 1
            self.agents[who].stats["trade_value"] += value
        self.notify(buy.agent, "trade", f"Bought {q} {g} from {sell.agent} @ {fmt(price)}.", other=sell.agent, valence=0.15, magnitude=value / 100)
        self.notify(sell.agent, "trade", f"Sold {q} {g} to {buy.agent} @ {fmt(price)} (fee {fmt(fee)}).", other=buy.agent, valence=0.15, magnitude=value / 100)
        self.emit("trade", f"{buy.agent} bought {q} {g} from {sell.agent} @ {fmt(price)}", actor=buy.agent, targets=[sell.agent],
                  importance=1, data={"good": g, "qty": q, "price": from_cents(price), "value": value, "buyer": buy.agent, "seller": sell.agent})

    def _remove_order(self, o: Order, refund: bool) -> None:
        self.orders.pop(o.id, None)
        book = self.books[o.good][o.side]
        if o in book:
            book.remove(o)
        if refund and o.qty > 0:
            if o.side == "buy":
                self.ledger.transfer(ESCROW, o.agent, cash=o.qty * o.price)
            else:
                self.ledger.transfer(ESCROW, o.agent, items={o.good: o.qty})

    def _a_cancel_order(self, aid: str, a: Action) -> None:
        o = self.resolve_id(a.order_id or a.contract_id, "O", self.orders, "order")
        if o.agent != aid:
            raise ActionError(f"order {o.id} is not yours")
        self._remove_order(o, refund=True)
        self.emit("order_cancelled", f"{aid} cancelled {o.id}", actor=aid, visibility="system", importance=0)

    def _a_craft(self, aid: str, a: Action) -> None:
        good = self._validate_good(a.item)
        if good not in RECIPES:
            raise ActionError(f"{good} cannot be crafted; craftable: {', '.join(RECIPES)}")
        qty = a.qty or 1
        if not 1 <= qty <= 50:
            raise ActionError("craft qty must be 1..50")
        need = {k: v * qty for k, v in RECIPES[good].items()}
        cost = int(round(self.mc.craft_cost * 100)) * qty
        if not self.ledger.can_afford(aid, cost, need):
            raise ActionError(f"crafting {qty} {good} needs {need} and {fmt(cost)} coins")
        self.ledger.burn(aid, cash=cost, items=need)
        self.ledger.mint(aid, items={good: qty})
        self.agents[aid].stats["crafted"] += qty
        self.emit("craft", f"{aid} crafted {qty} {good}", actor=aid, importance=0, data={"good": good, "qty": qty})

    def rules_text(self) -> str:
        mc = self.mc
        rec = "; ".join(f"{g} = " + " + ".join(f"{q} {k}" for k, q in r.items()) for g, r in RECIPES.items())
        return (
            "WORLD: THE OPEN MARKETPLACE\n"
            f"- Goods: raw {', '.join(RAW_GOODS)}; craftable {', '.join(RECIPES)}. Recipes: {rec} (plus {mc.craft_cost:.2f} coins labour per unit).\n"
            f"- Each step you produce your specialty raw good automatically.\n"
            + (f"- You must eat: at the end of each step you consume food automatically (1 bread feeds you 3 steps, 1 grain feeds you 1 step, bread first). "
               f"No food = hunger penalty of {mc.hunger_penalty:.2f} coins.\n" if mc.subsistence else "")
            + f"- Limit order book per good: buy/sell orders match at the resting order's price; sellers pay a {mc.trade_fee:.0%} fee. "
            f"Orders lock your coins/goods and expire after {mc.order_ttl} steps. Unmatched orders can be cancelled.\n"
            "- You may also barter or trade privately via contracts.\n"
            "- At the end, your SCORE = coins + each item you hold × YOUR private value for it. Values differ between agents, so trade can benefit both sides."
        )

    def action_reference(self) -> str:
        return (
            '{"type":"post_order","side":"buy"|"sell","item":"grain","qty":3,"price":6.5}\n'
            '{"type":"cancel_order","order_id":"O12"}\n'
            '{"type":"craft","item":"bread","qty":1}\n'
            + self.social_action_reference()
        )

    def _avg_price(self, g: str, window: int = 5) -> Optional[float]:
        pts = [(p, q) for s, p, q in self.price_history[g] if s > self.step - window]
        if not pts:
            return None
        return sum(p * q for p, q in pts) / sum(q for p, q in pts)

    def private_status(self, agent_id: str) -> str:
        vals = ", ".join(f"{g} {fmt(v)}" for g, v in self.valuation[agent_id].items())
        food = self.satiety[agent_id]
        fed = (f"You are fed for {food} more step(s)." if food else "You will need to eat at the end of this step (bread or grain).") if self.mc.subsistence else ""
        return (f"  specialty: you produce {self.specialty[agent_id]}. {fed}\n"
                f"  YOUR PRIVATE end-of-game values per unit: {vals}\n"
                f"  your current score (coins + items at your values): {fmt(self.final_score(agent_id))}")

    def render_world(self, agent_id: str) -> str:
        lines = ["MARKET (best bids / best asks, last trade, 5-step average):"]
        for g in GOODS:
            bids = self.books[g]["buy"][:2]
            asks = self.books[g]["sell"][:2]
            b = " ".join(f"{o.qty}@{fmt(o.price)}" for o in bids) or "-"
            s = " ".join(f"{o.qty}@{fmt(o.price)}" for o in asks) or "-"
            lp = fmt(self.last_price[g]) if self.last_price[g] else "none"
            av = self._avg_price(g)
            avs = fmt(int(av)) if av else "n/a"
            vol = self.vol_history[-1].get(g, 0) if self.vol_history else 0
            lines.append(f"  {g:<7} bids [{b}]  asks [{s}]  last {lp}  avg {avs}  vol(last step) {vol}")
        mine = [o for o in self.orders.values() if o.agent == agent_id]
        if mine:
            lines.append("YOUR RESTING ORDERS: " + "; ".join(f"{o.id} {o.side} {o.qty} {o.good} @ {fmt(o.price)} (exp {o.expires})" for o in mine))
        return "\n".join(lines)

    def observe(self, agent_id: str) -> dict:
        return {
            "env": self.key,
            "step": self.step,
            "cash": self.ledger.cash(agent_id),
            "inventory": self.ledger.items(agent_id),
            "specialty": self.specialty[agent_id],
            "valuation": dict(self.valuation[agent_id]),
            "satiety": self.satiety[agent_id],
            "best_bid": {g: (self.books[g]["buy"][0].price if self.books[g]["buy"] else None) for g in GOODS},
            "best_ask": {g: (self.books[g]["sell"][0].price if self.books[g]["sell"] else None) for g in GOODS},
            "ref_price": {g: int(self.ref_price[g]) for g in GOODS},
            "my_orders": [o.__dict__.copy() for o in self.orders.values() if o.agent == agent_id],
            "recipes": RECIPES,
        }

    def env_metrics(self) -> dict:
        holders = list(self.agents)
        supply = {}
        top_share = {}
        for g in GOODS:
            amounts = []
            for a in holders:
                q = self.ledger.qty(a, g) + self.locked_env_assets(a)[1].get(g, 0)
                amounts.append((q, a))
            tot = sum(q for q, _ in amounts)
            supply[g] = tot
            if tot:
                q, a = max(amounts)
                top_share[g] = {"agent": a, "share": q / tot}
        return {
            "prices": {g: round(self.ref_price[g] / 100, 2) for g in GOODS},
            "last_prices": {g: (from_cents(p) if p else None) for g, p in self.last_price.items()},
            "volume_units": dict(self.step_volume),
            "volume_value": from_cents(self.step_value),
            "supply": supply,
            "top_holder": top_share,
            "open_orders": len(self.orders),
            "hungry": sum(1 for a in self.agents if self.satiety[a] == 0 and self.ledger.qty(a, "bread") == 0 and self.ledger.qty(a, "grain") == 0),
        }
