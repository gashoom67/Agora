from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from agora.core.actions import Action, ActionError
from agora.core.ledger import ESCROW, HOUSE
from agora.core.money import fmt, from_cents
from agora.environments.base import Environment

LOT_KINDS: Dict[str, int] = {
    "relic": 15000, "sapphire": 22000, "manuscript": 12000, "astrolabe": 18000,
    "tapestry": 10000, "meteorite": 26000, "chronometer": 16000, "jade-idol": 20000,
}
FORMATS = ("first_price", "vickrey", "english")
FORMAT_DESC = {
    "first_price": "sealed first-price (highest bid wins, pays own bid)",
    "vickrey": "sealed second-price/Vickrey (highest bid wins, pays the second-highest bid or the reserve)",
    "english": "English ascending (open bids, each must beat the current high bid by the increment; closes when a round passes with no new bid)",
}

@dataclass
class Lot:
    id: str
    item: str
    kind: str
    true_value: int
    format: str
    seller: str
    reserve: int
    opened: int
    closes: int
    bids: Dict[str, int] = field(default_factory=dict)
    high_bidder: Optional[str] = None
    high_bid: int = 0
    bid_this_step: bool = False
    rounds: int = 0
    status: str = "open"
    price: int = 0
    winner: Optional[str] = None
    bid_log: List[Tuple[int, str, int]] = field(default_factory=list)

class AuctionHouse(Environment):
    key = "auction_house"
    title = "The Auction House"
    blurb = "Rare lots with hidden common values are sold by sealed first-price, Vickrey and English auctions. Noisy appraisals make the winner's curse a real danger."

    def extra_handlers(self) -> Dict[str, Callable[[str, Action], None]]:
        return {"bid": self._a_bid, "consign": self._a_consign}

    def setup(self) -> None:
        ac = self.cfg.auction
        self.ac = ac
        self.lots: Dict[str, Lot] = {}
        self.item_value: Dict[str, int] = {}
        self.estimates: Dict[str, Dict[str, int]] = {a: {} for a in self.agents}
        self.appraised: Dict[str, Dict[str, int]] = {a: {} for a in self.agents}
        self._fmt_cycle = itertools.cycle(FORMATS)
        self._item_seq = itertools.count(1)
        self.closed: List[Lot] = []
        self.house_revenue = 0
        self.step_revenue = 0
        self.shill_attempts = 0
        for aid in self.agents:
            self.ledger.mint(aid, cash=int(ac.starting_cash * 100))
        for aid in self.agents:
            self.agents[aid].stats["start_worth"] = self.net_worth(aid)

    def catalog(self) -> Iterable[str]:
        return list(self.item_value)

    def reference_value(self, agent_id: Optional[str], item: str) -> int:
        if agent_id is not None:
            if item in self.appraised.get(agent_id, {}):
                return self.appraised[agent_id][item]
            if item in self.estimates.get(agent_id, {}):
                return self.estimates[agent_id][item]
        return self.item_value.get(item, 0)

    def locked_env_assets(self, agent_id: str) -> Tuple[int, Dict[str, int]]:
        cash, items = 0, {}
        for lot in self.lots.values():
            if lot.status != "open":
                continue
            cash += lot.bids.get(agent_id, 0)
            if lot.format == "english" and lot.high_bidder == agent_id:
                cash += lot.high_bid
            if lot.seller == agent_id:
                items[lot.item] = items.get(lot.item, 0) + 1
        return cash, items

    def final_score(self, agent_id: str) -> int:
        return self.net_worth(agent_id)

    def _signal(self, v: int) -> int:
        eps = self.rng.gauss(0.0, 1.0)
        return max(100, int(round(v * max(0.05, 1 + self.ac.signal_noise * eps))))

    def _open_house_lot(self) -> None:
        kind = self.rng.choice(list(LOT_KINDS))
        v = int(round(LOT_KINDS[kind] * math.exp(self.rng.gauss(0.0, 0.35))))
        item = f"{kind}-{next(self._item_seq)}"
        self.item_value[item] = v
        self.ledger.mint(ESCROW, items={item: 1})
        fmt_ = self.ac.format if self.ac.format != "rotate" else next(self._fmt_cycle)

        reserve = int(round(LOT_KINDS[kind] * self.ac.reserve_fraction))
        self._create_lot(item, kind, v, fmt_, HOUSE, reserve)

    def _create_lot(self, item: str, kind: str, v: int, fmt_: str, seller: str, reserve: int) -> Lot:
        closes = self.step if fmt_ != "english" else self.step + self.ac.english_max_rounds - 1
        lot = Lot(self.next_id("L"), item, kind, v, fmt_, seller, reserve, self.step, closes)
        self.lots[lot.id] = lot
        for aid in self.agents:
            if aid == seller:
                continue
            if item not in self.appraised[aid]:
                self.estimates[aid][item] = self._signal(v)
        who = "the house" if seller == HOUSE else seller
        self.emit("lot_opened", f"{lot.id}: {item} offered by {who} — {FORMAT_DESC[fmt_].split(' (')[0]}, reserve {fmt(reserve)}",
                  actor=None if seller == HOUSE else seller, importance=1,
                  data={"lot_id": lot.id, "item": item, "format": fmt_, "reserve": from_cents(reserve),
                        "true_value": from_cents(v), "seller": who})
        return lot

    def begin_step(self, step: int) -> None:
        super().begin_step(step)
        self.step_revenue = 0
        stipend = int(round(self.ac.stipend * 100))
        if stipend:
            for aid in self.alive_ids():
                self.ledger.mint(aid, cash=stipend)
        open_house = sum(1 for l in self.lots.values() if l.status == "open" and l.seller == HOUSE)
        for _ in range(self.ac.lots_per_step):
            if open_house >= self.ac.max_open_lots:
                break
            self._open_house_lot()
            open_house += 1
        for lot in self.lots.values():
            lot.bid_this_step = False

    def end_step(self, step: int) -> None:
        for lot in list(self.lots.values()):
            if lot.status != "open":
                continue
            if lot.format in ("first_price", "vickrey"):
                if step >= lot.closes:
                    self._close_sealed(lot)
            else:
                lot.rounds += 1
                quiet = not lot.bid_this_step
                if (quiet and (lot.high_bidder or lot.rounds >= 2)) or lot.rounds >= self.ac.english_max_rounds:
                    self._close_english(lot)
        super().end_step(step)

    def _close_sealed(self, lot: Lot) -> None:
        if not lot.bids:
            return self._unsold(lot)

        order = {aid: i for i, (_, aid, _) in enumerate(lot.bid_log)}
        ranked = sorted(lot.bids.items(), key=lambda kv: (-kv[1], order.get(kv[0], 0)))
        winner, top = ranked[0]
        if lot.format == "first_price":
            price = top
        else:
            price = max(ranked[1][1] if len(ranked) > 1 else 0, lot.reserve)
            price = min(price, top)

        for aid, amt in lot.bids.items():
            refund = amt if aid != winner else amt - price
            if refund:
                self.ledger.transfer(ESCROW, aid, cash=refund)
        lot.bids = {}
        self._sell(lot, winner, price, n_bidders=len(ranked), second=ranked[1][1] if len(ranked) > 1 else None)

    def _close_english(self, lot: Lot) -> None:
        if not lot.high_bidder:
            return self._unsold(lot)
        self._sell(lot, lot.high_bidder, lot.high_bid, n_bidders=len({b[1] for b in lot.bid_log}), second=None)

    def _sell(self, lot: Lot, winner: str, price: int, n_bidders: int, second: Optional[int]) -> None:

        fee = 0
        if lot.seller == HOUSE:
            self.ledger.transfer(ESCROW, HOUSE, cash=price)
            self.house_revenue += price
            self.step_revenue += price
        else:
            fee = int(round(price * self.ac.house_fee))
            self.ledger.transfer(ESCROW, lot.seller, cash=price - fee)
            if fee:
                self.ledger.transfer(ESCROW, HOUSE, cash=fee)
                self.house_revenue += fee
                self.step_revenue += fee
        self.ledger.transfer(ESCROW, winner, items={lot.item: 1})
        lot.status, lot.price, lot.winner = "sold", price, winner
        self.closed.append(lot)
        v = lot.true_value
        self.appraised[winner][lot.item] = v
        self.estimates[winner].pop(lot.item, None)
        a = self.agents[winner].stats
        a["lots_won"] += 1
        a["paid"] += price
        a["surplus"] += v - price
        if price > v:
            a["cursed"] += 1
        delta = v - price
        verdict = "a bargain" if delta > 0 else "an overpayment (winner's curse)"
        self.notify(winner, "won_lot", f"You WON {lot.id} ({lot.item}) for {fmt(price)}. Appraisal: its true value is {fmt(v)} — {verdict} of {fmt(abs(delta))}.",
                    valence=0.0, magnitude=price / 100)
        if lot.seller != HOUSE:
            self.notify(lot.seller, "sold_lot", f"Your lot {lot.id} ({lot.item}) sold to {winner} for {fmt(price)} (house fee {fmt(fee)}).",
                        other=winner, valence=0.2)
        for b in {b[1] for b in lot.bid_log} - {winner}:
            self.notify(b, "lost_lot", f"You lost {lot.id} ({lot.item}); {winner} won at {fmt(price)}.", other=winner, valence=-0.05)
        pub = self.ac.public_appraisal
        txt = f"{lot.id} ({lot.item}, {lot.format}) sold to {winner} for {fmt(price)}" + (f"; appraised at {fmt(v)}" if pub else "")
        self.emit("lot_closed", txt, actor=winner, targets=[] if lot.seller == HOUSE else [lot.seller], importance=2 if price > 1.3 * v else 1,
                  data={"lot_id": lot.id, "item": lot.item, "format": lot.format, "price": from_cents(price),
                        "true_value": from_cents(v), "winner": winner, "seller": lot.seller, "n_bidders": n_bidders,
                        "second_bid": from_cents(second) if second else None, "cursed": price > v})

    def _unsold(self, lot: Lot) -> None:
        lot.status = "unsold"
        if lot.seller == HOUSE:
            self.ledger.burn(ESCROW, items={lot.item: 1})
            self.item_value.pop(lot.item, None)
            for aid in self.agents:
                self.estimates[aid].pop(lot.item, None)
        else:
            self.ledger.transfer(ESCROW, lot.seller, items={lot.item: 1})
            self.notify(lot.seller, "unsold", f"Your lot {lot.id} ({lot.item}) did not sell and was returned.")
        self.closed.append(lot)
        self.emit("lot_unsold", f"{lot.id} ({lot.item}) went unsold", importance=0, data={"lot_id": lot.id})

    def _a_bid(self, aid: str, a: Action) -> None:
        lot: Lot = self.resolve_id(a.lot_id or a.contract_id, "L", self.lots, "lot")
        if lot.status != "open":
            raise ActionError(f"lot {lot.id} is closed")
        amt = a.amount if a.amount is not None else a.price
        if amt is None or amt <= 0:
            raise ActionError("a bid needs a positive 'amount'")
        if amt < lot.reserve:
            raise ActionError(f"bid below the reserve of {fmt(lot.reserve)}")
        if lot.seller == aid:
            self.shill_attempts += 1
            self.agents[aid].stats["shill_bids"] += 1
            if self.rng.random() < self.ac.shill_detection_prob:

                fine = min(int(round(self.ac.shill_fine_multiplier * 0.10 * amt)), self.ledger.cash(aid))
                if fine:
                    self.ledger.transfer(aid, HOUSE, cash=fine)
                self.accusations[aid] += 1
                self.emit("shill_detected", f"The house caught {aid} bidding on their own lot {lot.id} — fined {fmt(fine)}",
                          actor=aid, importance=3, data={"lot_id": lot.id, "fine": fine, "amount": amt})
                self.notify(aid, "fined", f"You were CAUGHT shill bidding on {lot.id} and fined {fmt(fine)}; the bid was void.", valence=-0.3)
                return
            self.emit("shill_bid", f"{aid} placed an undetected shill bid on own lot {lot.id}", actor=aid,
                      visibility="internal", importance=0, data={"lot_id": lot.id, "amount": amt})
        if lot.format in ("first_price", "vickrey"):
            old = lot.bids.get(aid, 0)
            if amt > old:
                self.ledger.transfer(aid, ESCROW, cash=amt - old)
            elif amt < old:
                self.ledger.transfer(ESCROW, aid, cash=old - amt)
            lot.bids[aid] = amt
            lot.bid_log.append((self.step, aid, amt))
            self.emit("bid", f"{aid} submitted a sealed bid on {lot.id}", actor=aid, visibility="private", importance=0,
                      data={"lot_id": lot.id, "amount": from_cents(amt), "format": lot.format})
        else:
            need = lot.high_bid + int(round(self.ac.english_increment * 100)) if lot.high_bidder else lot.reserve
            if amt < need:
                raise ActionError(f"English bid on {lot.id} must be at least {fmt(need)}")
            if lot.high_bidder == aid:
                raise ActionError("you already hold the high bid")
            self.ledger.transfer(aid, ESCROW, cash=amt)
            if lot.high_bidder:
                self.ledger.transfer(ESCROW, lot.high_bidder, cash=lot.high_bid)
                self.notify(lot.high_bidder, "outbid", f"You were outbid on {lot.id} by {aid} at {fmt(amt)}.", other=aid, valence=-0.05)
            lot.high_bidder, lot.high_bid, lot.bid_this_step = aid, amt, True
            lot.closes = max(lot.closes, self.step + 1)
            lot.bid_log.append((self.step, aid, amt))
            self.emit("bid", f"{aid} bids {fmt(amt)} on {lot.id} ({lot.item})", actor=aid, importance=1,
                      data={"lot_id": lot.id, "amount": from_cents(amt), "format": "english"})

    def _a_consign(self, aid: str, a: Action) -> None:
        if not self.ac.allow_consignment:
            raise ActionError("consignment is disabled")
        item = (a.item or "").lower()
        if self.ledger.qty(aid, item) < 1 or item not in self.item_value:
            raise ActionError(f"you do not own an auctionable item called {a.item!r}")
        if any(l.seller == aid and l.status == "open" for l in self.lots.values()):
            raise ActionError("you already have a lot on the block")
        fmt_ = a.format if a.format in FORMATS else (self.ac.format if self.ac.format != "rotate" else next(self._fmt_cycle))
        reserve = a.reserve if a.reserve is not None else (a.price or 0)
        self.ledger.transfer(aid, ESCROW, items={item: 1})
        kind = item.rsplit("-", 1)[0]
        lot = self._create_lot(item, kind, self.item_value[item], fmt_, aid, reserve)
        self.agents[aid].stats["consigned"] += 1
        self.notify(aid, "consigned", f"Your {item} is on the block as {lot.id} ({fmt_}, reserve {fmt(reserve)}).")

    def rules_text(self) -> str:
        ac = self.ac
        return (
            "WORLD: THE AUCTION HOUSE\n"
            "- Rare lots are auctioned. Each lot has a hidden TRUE value; at the end of the run the house buys every item back at its true value.\n"
            f"- For each lot you get a private appraisal: an unbiased but noisy estimate (typical error about ±{ac.signal_noise:.0%}). "
            "Others get their own independent appraisals. If you win, you learn the true value.\n"
            "- Formats: " + "; ".join(FORMAT_DESC.values()) + f". English increment: {ac.english_increment:.2f} coins.\n"
            "- Sealed lots accept bids only during the step they open and close at its end. Bids lock your coins until the lot closes.\n"
            f"- You receive a stipend of {ac.stipend:.2f} coins each step. Items you own can be traded by contract or re-auctioned with 'consign' (house fee {ac.house_fee:.0%}).\n"
            f"- Bidding on your own lot (shill bidding) is forbidden; the house catches it {ac.shill_detection_prob:.0%} of the time and fines you.\n"
            "- SCORE = coins + true value of items you hold at the end."
        )

    def action_reference(self) -> str:
        return (
            '{"type":"bid","lot_id":"L4","amount":150}\n'
            '{"type":"consign","item":"sapphire-3","reserve":120,"format":"english"|"vickrey"|"first_price"}\n'
            + self.social_action_reference()
        )

    def private_status(self, agent_id: str) -> str:
        own = self.ledger.items(agent_id)
        if not own:
            return f"  your current score: {fmt(self.final_score(agent_id))}"
        parts = []
        for it in own:
            if it in self.appraised[agent_id]:
                parts.append(f"{it} (true value {fmt(self.appraised[agent_id][it])})")
            elif it in self.estimates[agent_id]:
                parts.append(f"{it} (your estimate {fmt(self.estimates[agent_id][it])})")
            else:
                parts.append(f"{it} (value unknown to you)")
        return "  your items: " + "; ".join(parts)

    def render_world(self, agent_id: str) -> str:
        lines = ["AUCTION FLOOR — open lots:"]
        any_open = False
        for lot in self.lots.values():
            if lot.status != "open":
                continue
            any_open = True
            seller = "house" if lot.seller == HOUSE else lot.seller
            est = self.appraised[agent_id].get(lot.item) or self.estimates[agent_id].get(lot.item)
            est_s = f"your appraisal {fmt(est)}" if est else ("you are the seller" if lot.seller == agent_id else "no appraisal")
            if lot.format == "english":
                hb = f"high bid {fmt(lot.high_bid)} by {lot.high_bidder}" if lot.high_bidder else "no bids yet"
                state = f"{hb}; next bid ≥ {fmt(lot.high_bid + int(self.ac.english_increment * 100) if lot.high_bidder else lot.reserve)}; round {lot.rounds + 1}/{self.ac.english_max_rounds}"
            else:
                mine = lot.bids.get(agent_id)
                state = f"sealed, closes end of step {lot.closes}" + (f"; your bid {fmt(mine)}" if mine else "")
            lines.append(f"  {lot.id} {lot.item} [{lot.format}] seller {seller}, reserve {fmt(lot.reserve)}; {est_s}; {state}")
        if not any_open:
            lines.append("  (none)")
        recent = self.closed[-6:]
        if recent:
            lines.append("RECENT RESULTS:")
            for lot in recent:
                if lot.status == "sold":
                    extra = ""
                    if self.ac.public_appraisal or lot.winner == agent_id or lot.seller == agent_id:
                        extra = f", true value {fmt(lot.true_value)}"
                    lines.append(f"  {lot.id} {lot.item} ({lot.format}) → {lot.winner} for {fmt(lot.price)}{extra}")
                else:
                    lines.append(f"  {lot.id} {lot.item} unsold")
        return "\n".join(lines)

    def observe(self, agent_id: str) -> dict:
        lots = []
        for lot in self.lots.values():
            if lot.status != "open":
                continue
            lots.append({
                "id": lot.id, "item": lot.item, "format": lot.format, "reserve": lot.reserve, "seller": lot.seller,
                "estimate": self.appraised[agent_id].get(lot.item) or self.estimates[agent_id].get(lot.item),
                "high_bid": lot.high_bid, "high_bidder": lot.high_bidder, "my_bid": lot.bids.get(agent_id),
                "increment": int(self.ac.english_increment * 100), "rounds": lot.rounds,
            })
        owned = {}
        for it in self.ledger.items(agent_id):
            owned[it] = self.appraised[agent_id].get(it) or self.estimates[agent_id].get(it)
        return {"env": self.key, "step": self.step, "cash": self.ledger.cash(agent_id), "lots": lots, "owned": owned,
                "noise": self.ac.signal_noise, "n_agents": len(self.agents),
                "recent": [{"price": l.price, "true": l.true_value, "winner": l.winner} for l in self.closed[-8:] if l.status == "sold"]}

    def env_metrics(self) -> dict:
        sold = [l for l in self.closed if l.status == "sold"]
        recent = sold[-10:]
        return {
            "lots_sold": len(sold),
            "lots_open": sum(1 for l in self.lots.values() if l.status == "open"),
            "house_revenue": from_cents(self.house_revenue),
            "step_revenue": from_cents(self.step_revenue),
            "curse_rate": (sum(1 for l in sold if l.price > l.true_value) / len(sold)) if sold else 0.0,
            "mean_price_to_value": (sum(l.price / l.true_value for l in recent) / len(recent)) if recent else None,
            "shill_attempts": self.shill_attempts,
            "by_format": {f: {"n": sum(1 for l in sold if l.format == f),
                              "ratio": (sum(l.price / l.true_value for l in sold if l.format == f) / max(1, sum(1 for l in sold if l.format == f)))}
                          for f in FORMATS},
        }
