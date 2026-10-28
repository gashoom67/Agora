from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agora.core.money import to_cents


class ActionParseError(ValueError):
    pass


class ActionError(Exception):


ALIASES: Dict[str, str] = {
    "broadcast": "say", "announce": "say", "post": "say", "public_message": "say", "speak": "say",
    "dm": "message", "private_message": "message", "whisper": "message", "send_message": "message", "tell": "message",
    "order": "post_order", "limit_order": "post_order", "place_order": "post_order",
    "buy": "post_order", "sell": "post_order",
    "cancel": "cancel_order",
    "offer": "propose_contract", "propose": "propose_contract", "contract": "propose_contract", "propose_trade": "propose_contract",
    "accept": "accept_contract", "reject": "reject_contract", "decline": "reject_contract",
    "betray": "renege", "default": "renege",
    "accuse_agent": "accuse", "report": "accuse",
    "create_syndicate": "form_syndicate", "found_syndicate": "form_syndicate",
    "invite_to_syndicate": "invite", "join": "join_syndicate", "leave": "leave_syndicate",
    "syndicate_message": "syndicate_say",
    "place_bid": "bid", "raise": "bid",
    "fish": "harvest", "catch": "harvest",
    "propose_rule_change": "propose_rule", "rule": "propose_rule",
    "cast_vote": "vote",
    "wait": "pass", "noop": "pass", "none": "pass", "idle": "pass", "hold": "pass",
    "make": "craft", "produce": "craft",
    "sell_lot": "consign", "list_item": "consign",
    "replenish": "restock", "donate_fish": "restock",
}

COMMON_TYPES = {
    "say", "message", "propose_contract", "accept_contract", "reject_contract", "renege",
    "accuse", "form_syndicate", "invite", "join_syndicate", "leave_syndicate", "syndicate_say", "pass",
}


@dataclass
class Bundle:
    cash: int = 0
    items: Dict[str, int] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return self.cash == 0 and not any(self.items.values())

    def describe(self) -> str:
        parts = []
        if self.cash:
            parts.append(f"{self.cash / 100:.2f} coins")
        for k, v in self.items.items():
            if v:
                parts.append(f"{v} {k}")
        return " + ".join(parts) if parts else "nothing"

    def to_dict(self) -> dict:
        return {"cash": round(self.cash / 100, 2), "items": dict(self.items)}


@dataclass
class Action:
    type: str
    raw: Dict[str, Any] = field(default_factory=dict)
    target: Optional[str] = None
    item: Optional[str] = None
    side: Optional[str] = None
    qty: Optional[int] = None
    price: Optional[int] = None
    amount: Optional[int] = None
    harvest: Optional[float] = None
    message: Optional[str] = None
    contract_id: Optional[str] = None
    lot_id: Optional[str] = None
    order_id: Optional[str] = None
    proposal_id: Optional[str] = None
    offer: Bundle = field(default_factory=Bundle)
    request: Bundle = field(default_factory=Bundle)
    settlement: str = "escrow"
    delay: int = 1
    rule: Optional[str] = None
    value: Optional[float] = None
    vote: Optional[bool] = None
    name: Optional[str] = None
    reserve: Optional[int] = None
    format: Optional[str] = None
    reason: Optional[str] = None

    def summary(self) -> str:
        bits = [self.type]
        for k in ("target", "side", "item", "qty", "contract_id", "lot_id", "order_id", "proposal_id", "name", "rule"):
            v = getattr(self, k)
            if v not in (None, ""):
                bits.append(f"{k}={v}")
        if self.price is not None:
            bits.append(f"price={self.price / 100:.2f}")
        if self.amount is not None:
            bits.append(f"amount={self.amount / 100:.2f}")
        if self.harvest is not None:
            bits.append(f"amount={self.harvest:g}")
        return " ".join(bits)


_ID_RE = re.compile(r"[A-Za-z]+-?\d+")


def _clean_str(v: Any, limit: int = 2000) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s[:limit] if s else None


def _clean_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ActionParseError("expected a number, got a boolean")
    try:
        f = float(str(v).replace(",", "").strip())
    except ValueError:
        raise ActionParseError(f"expected a whole number, got {v!r}") from None
    if f != f or f in (float("inf"), float("-inf")):
        raise ActionParseError("number must be finite")
    return int(round(f))


def _clean_money(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return to_cents(v)
    except (ValueError, TypeError):
        raise ActionParseError(f"expected an amount of coins, got {v!r}") from None


def _clean_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", "").strip())
    except ValueError:
        raise ActionParseError(f"expected a number, got {v!r}") from None
    if f != f or f in (float("inf"), float("-inf")):
        raise ActionParseError("number must be finite")
    return f


def _clean_id(v: Any) -> Optional[str]:
    s = _clean_str(v, 64)
    if s is None:
        return None
    s = s.strip("#").strip()
    return s


def parse_bundle(v: Any) -> Bundle:
    if v is None or v == "":
        return Bundle()
    if isinstance(v, (int, float, str)) and not isinstance(v, bool):
        return Bundle(cash=_clean_money(v) or 0)
    if not isinstance(v, dict):
        raise ActionParseError("offer/request must be an object like {cash, items}")
    cash = _clean_money(v.get("cash", v.get("coins", 0))) or 0
    items: Dict[str, int] = {}
    raw_items = v.get("items")
    if isinstance(raw_items, list):
        for it in raw_items:
            if isinstance(it, dict):
                name = _clean_str(it.get("name") or it.get("item"), 64)
                q = _clean_int(it.get("qty", it.get("quantity", 1)))
                if name and q:
                    items[name.lower()] = items.get(name.lower(), 0) + q
    elif isinstance(raw_items, dict):
        for name, q in raw_items.items():
            qi = _clean_int(q)
            if qi:
                items[str(name).lower()] = items.get(str(name).lower(), 0) + qi
    elif raw_items is None:
        for name, q in v.items():
            if name in ("cash", "coins", "items"):
                continue
            if isinstance(q, (int, float)) and not isinstance(q, bool):
                items[str(name).lower()] = int(q)
    if cash < 0 or any(q < 0 for q in items.values()):
        raise ActionParseError("offer/request amounts must be non-negative")
    return Bundle(cash=cash, items={k: q for k, q in items.items() if q})


def normalize_action(raw: Any, message_limit: int = 280) -> Action:
    if not isinstance(raw, dict):
        raise ActionParseError("each action must be a JSON object with a 'type'")
    t = _clean_str(raw.get("type") or raw.get("action"), 40)
    if not t:
        raise ActionParseError("action is missing 'type'")
    t0 = t.lower().replace(" ", "_").replace("-", "_")
    t = ALIASES.get(t0, t0)

    a = Action(type=t, raw=dict(raw))
    a.target = _clean_id(raw.get("target") or raw.get("to") or raw.get("agent") or raw.get("counterparty"))
    item = _clean_str(raw.get("item") or raw.get("good"), 64)
    a.item = item.lower() if item else None
    side = _clean_str(raw.get("side"), 8)
    if t0 in ("buy", "sell") and not side:
        side = t0
    a.side = side.lower() if side else None
    a.qty = _clean_int(raw.get("qty", raw.get("quantity")))
    a.price = _clean_money(raw.get("price"))
    if t == "harvest":
        a.harvest = _clean_float(raw.get("amount", raw.get("qty", raw.get("quantity"))))
    elif t == "restock":
        a.harvest = _clean_float(raw.get("amount", raw.get("qty")))
    else:
        a.amount = _clean_money(raw.get("amount", raw.get("bid")))
    msg = raw.get("message", raw.get("text", raw.get("content")))
    a.message = _clean_str(msg, message_limit)
    a.contract_id = _clean_id(raw.get("contract_id") or raw.get("contract"))
    a.lot_id = _clean_id(raw.get("lot_id") or raw.get("lot"))
    a.order_id = _clean_id(raw.get("order_id"))
    a.proposal_id = _clean_id(raw.get("proposal_id") or raw.get("proposal"))
    a.offer = parse_bundle(raw.get("offer", raw.get("give")))
    a.request = parse_bundle(raw.get("request", raw.get("want", raw.get("ask"))))
    st = (_clean_str(raw.get("settlement"), 16) or "escrow").lower()
    a.settlement = "trust" if st.startswith("trust") or st in ("deferred", "promise", "credit") else "escrow"
    a.delay = max(1, _clean_int(raw.get("delay")) or 1)
    rule = _clean_str(raw.get("rule"), 32)
    a.rule = rule.lower() if rule else None
    a.value = _clean_float(raw.get("value"))
    vote = raw.get("vote", raw.get("choice"))
    if isinstance(vote, bool):
        a.vote = vote
    elif vote is not None:
        a.vote = str(vote).strip().lower() in ("yes", "y", "true", "for", "aye", "1", "approve")
    a.name = _clean_str(raw.get("name") or raw.get("syndicate"), 40)
    a.reserve = _clean_money(raw.get("reserve"))
    fmt = _clean_str(raw.get("format"), 16)
    a.format = fmt.lower() if fmt else None
    a.reason = _clean_str(raw.get("reason"), message_limit)

    for fld in ("qty", "price", "amount", "reserve"):
        v = getattr(a, fld)
        if v is not None and v < 0:
            raise ActionParseError(f"{fld} must be non-negative")
    if a.harvest is not None and a.harvest < 0:
        raise ActionParseError("amount must be non-negative")
    return a


def normalize_actions(raw_list: Any, limit: int, message_limit: int = 280) -> Tuple[List[Action], List[str]]:
    if raw_list is None:
        return [], []
    if isinstance(raw_list, dict):
        raw_list = [raw_list]
    if not isinstance(raw_list, list):
        return [], ["'actions' must be a list"]
    out: List[Action] = []
    errs: List[str] = []
    for i, raw in enumerate(raw_list):
        if len(out) >= limit:
            errs.append(f"only {limit} actions allowed per turn; extra actions ignored")
            break
        try:
            out.append(normalize_action(raw, message_limit))
        except ActionParseError as e:
            errs.append(f"action #{i + 1}: {e}")
    return out, errs
