from __future__ import annotations

import random
from abc import ABC, abstractmethod
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Iterable, List, Optional, Set, Tuple

from agora.agents.persona import Persona
from agora.config import SimulationConfig
from agora.core.actions import Action, ActionError, Bundle
from agora.core.events import Event
from agora.core.ledger import ESCROW, HOUSE, InsufficientFunds, Ledger
from agora.core.money import fmt

@dataclass
class Notice:
    

    step: int
    kind: str
    text: str
    other: Optional[str] = None
    valence: float = 0.0
    magnitude: float = 0.0

@dataclass
class AgentState:
    id: str
    persona: Persona
    alive: bool = True
    stats: Counter = field(default_factory=Counter)

@dataclass
class Contract:
    id: str
    proposer: str
    counterparty: str
    offer: Bundle
    request: Bundle
    settlement: str
    delay: int
    created_step: int
    expires_step: int
    message: str = ""
    status: str = "pending"
    due_step: Optional[int] = None

    def describe(self) -> str:
        how = "escrow (atomic swap)" if self.settlement == "escrow" else f"trust: {self.counterparty} pays first, {self.proposer} delivers within {self.delay} step(s)"
        return f"{self.id}: {self.proposer} gives [{self.offer.describe()}] for {self.counterparty}'s [{self.request.describe()}] via {how}"

@dataclass
class Syndicate:
    name: str
    founder: str
    members: Set[str]
    invites: Set[str]
    created_step: int

@dataclass
class PublicPost:
    step: int
    author: str
    text: str

class Environment(ABC):
    key: str = "base"
    title: str = "Base"
    blurb: str = ""

    def __init__(self, cfg: SimulationConfig, personas: List[Persona], rng: random.Random) -> None:
        self.cfg = cfg
        self.mech = cfg.mechanism
        self.rng = rng
        self.ledger = Ledger()
        self.step = 0
        self.agents: Dict[str, AgentState] = {}
        for p in personas:
            self.agents[p.id] = AgentState(p.id, p)
            self.ledger.open(p.id)
        self._name_index = {aid.lower(): aid for aid in self.agents}
        self.contracts: Dict[str, Contract] = {}
        self.syndicates: Dict[str, Syndicate] = {}
        self.membership: Dict[str, str] = {}
        self.board: Deque[PublicPost] = deque(maxlen=40)
        self.notices: Dict[str, List[Notice]] = {a: [] for a in self.agents}
        self.default_registry: Counter = Counter()
        self.renege_registry: Counter = Counter()
        self.accusations: Counter = Counter()
        self.pending_events: List[Event] = []
        self._seq: Counter = Counter()
        self._handlers: Dict[str, Callable[[str, Action], None]] = {
            "say": self._a_say,
            "message": self._a_message,
            "propose_contract": self._a_propose_contract,
            "accept_contract": self._a_accept_contract,
            "reject_contract": self._a_reject_contract,
            "renege": self._a_renege,
            "accuse": self._a_accuse,
            "form_syndicate": self._a_form_syndicate,
            "invite": self._a_invite,
            "join_syndicate": self._a_join_syndicate,
            "leave_syndicate": self._a_leave_syndicate,
            "syndicate_say": self._a_syndicate_say,
            "pass": lambda aid, a: None,
        }
        self._handlers.update(self.extra_handlers())

    @abstractmethod
    def extra_handlers(self) -> Dict[str, Callable[[str, Action], None]]: ...

    @abstractmethod
    def setup(self) -> None:
        

    def begin_step(self, step: int) -> None:
        self.step = step

    def end_step(self, step: int) -> None:
        self._settle_contracts(step)

    @abstractmethod
    def catalog(self) -> Iterable[str]:
        

    @abstractmethod
    def reference_value(self, agent_id: Optional[str], item: str) -> int:
        

    @abstractmethod
    def rules_text(self) -> str: ...

    @abstractmethod
    def action_reference(self) -> str: ...

    @abstractmethod
    def render_world(self, agent_id: str) -> str: ...

    @abstractmethod
    def observe(self, agent_id: str) -> dict:
        

    def env_metrics(self) -> dict:
        return {}

    def final_score(self, agent_id: str) -> int:
        return self.net_worth(agent_id)

    def private_status(self, agent_id: str) -> str:
        return ""

    def locked_env_assets(self, agent_id: str) -> Tuple[int, Dict[str, int]]:
        
        return 0, {}

    def next_id(self, prefix: str) -> str:
        self._seq[prefix] += 1
        return f"{prefix}{self._seq[prefix]}"

    def emit(self, kind: str, text: str = "", actor: Optional[str] = None, targets: Optional[List[str]] = None,
             data: Optional[dict] = None, visibility: str = "public", importance: int = 1) -> Event:
        ev = Event(kind=kind, step=self.step, text=text, actor=actor, targets=list(targets or []),
                   data=dict(data or {}), visibility=visibility, importance=importance)
        self.pending_events.append(ev)
        return ev

    def drain_events(self) -> List[Event]:
        out, self.pending_events = self.pending_events, []
        return out

    def notify(self, agent_id: str, kind: str, text: str, other: Optional[str] = None,
               valence: float = 0.0, magnitude: float = 0.0) -> None:
        if agent_id in self.notices:
            self.notices[agent_id].append(Notice(self.step, kind, text, other, valence, magnitude))

    def pop_notices(self, agent_id: str) -> List[Notice]:
        out = self.notices.get(agent_id, [])
        self.notices[agent_id] = []
        return out

    def resolve_agent(self, name: Optional[str]) -> str:
        if not name:
            raise ActionError("a 'target' agent name is required")
        aid = self._name_index.get(str(name).strip().lstrip("@").lower())
        if aid is None:
            raise ActionError(f"there is no agent called {name!r}")
        return aid

    def resolve_id(self, raw: Optional[str], prefix: str, table: dict, what: str):
        if not raw:
            raise ActionError(f"a {what} id is required")
        key = str(raw).strip().upper()
        if key.isdigit():
            key = prefix + key
        if key not in table:
            raise ActionError(f"no {what} with id {raw!r}")
        return table[key]

    def alive_ids(self) -> List[str]:
        return [a for a, s in self.agents.items() if s.alive]

    def bundle_value(self, b: Bundle, agent_id: Optional[str] = None) -> int:
        return b.cash + sum(q * self.reference_value(agent_id, it) for it, q in b.items.items())

    def _check_items_known(self, b: Bundle) -> None:
        known = set(self.catalog())
        for it in b.items:
            if it not in known and not self._owned_by_anyone(it):
                raise ActionError(f"unknown item {it!r}")

    def _owned_by_anyone(self, item: str) -> bool:
        return any(self.ledger.qty(a, item) > 0 for a in self.agents)

    def locked_assets(self, agent_id: str) -> Tuple[int, Dict[str, int]]:
        cash, items = self.locked_env_assets(agent_id)
        items = Counter(items)
        for c in self.contracts.values():
            if c.status == "pending" and c.settlement == "escrow" and c.proposer == agent_id:
                cash += c.offer.cash
                items.update(c.offer.items)
        return cash, dict(items)

    def net_worth(self, agent_id: str) -> int:
        
        cash = self.ledger.cash(agent_id)
        val = cash
        for it, q in self.ledger.items(agent_id).items():
            val += q * self.reference_value(None, it)
        lc, li = self.locked_assets(agent_id)
        val += lc
        for it, q in li.items():
            val += q * self.reference_value(None, it)
        return val

    def action_types(self) -> List[str]:
        types = list(self._handlers)
        if not self.mech.allow_private_messages:
            types.remove("message")
        if not self.mech.allow_syndicates:
            for t in ("form_syndicate", "invite", "join_syndicate", "leave_syndicate", "syndicate_say"):
                types.remove(t)
        return types

    def handle(self, agent_id: str, action: Action) -> None:
        if not self.agents[agent_id].alive:
            raise ActionError("you are no longer active")
        if action.type not in self.action_types():
            raise ActionError(f"unknown or disabled action type {action.type!r}")
        try:
            self._handlers[action.type](agent_id, action)
        except InsufficientFunds as e:
            raise ActionError(f"insufficient funds or goods ({e})") from None
        self.agents[agent_id].stats[f"act_{action.type}"] += 1

    def _a_say(self, aid: str, a: Action) -> None:
        if not a.message:
            raise ActionError("'say' needs a message")
        self.board.append(PublicPost(self.step, aid, a.message))
        self.emit("say", a.message, actor=aid, visibility="public", importance=1)

    def _a_message(self, aid: str, a: Action) -> None:
        if not self.mech.allow_private_messages:
            raise ActionError("private messages are disabled in this world")
        tgt = self.resolve_agent(a.target)
        if tgt == aid:
            raise ActionError("you cannot message yourself")
        if not a.message:
            raise ActionError("'message' needs a message")
        self.notify(tgt, "dm", f'{aid} (private): "{a.message}"', other=aid, valence=0.05)
        self.emit("dm", a.message, actor=aid, targets=[tgt], visibility="private", importance=1)

    def _a_propose_contract(self, aid: str, a: Action) -> None:
        tgt = self.resolve_agent(a.target)
        if tgt == aid:
            raise ActionError("you cannot contract with yourself")
        if a.offer.is_empty() and a.request.is_empty():
            raise ActionError("a contract needs an 'offer' and/or a 'request'")
        self._check_items_known(a.offer)
        self._check_items_known(a.request)
        if a.settlement == "trust" and not 1 <= a.delay <= self.mech.max_trust_delay:
            raise ActionError(f"trust delay must be between 1 and {self.mech.max_trust_delay}")
        open_count = sum(1 for c in self.contracts.values() if c.proposer == aid and c.status == "pending")
        if open_count >= 6:
            raise ActionError("you already have 6 pending proposals; wait for answers")
        if a.settlement == "escrow":

            self.ledger.transfer(aid, ESCROW, a.offer.cash, a.offer.items)
        cid = self.next_id("C")
        c = Contract(cid, aid, tgt, a.offer, a.request, a.settlement, a.delay if a.settlement == "trust" else 0,
                     self.step, self.step + self.mech.contract_ttl, message=a.message or "")
        self.contracts[cid] = c
        note = f' — "{c.message}"' if c.message else ""
        self.notify(tgt, "contract_offer", f"New contract offer {c.describe()}{note}. Reply with accept_contract or reject_contract.",
                    other=aid, valence=0.05)
        self.emit("contract_proposed", c.describe(), actor=aid, targets=[tgt], visibility="private", importance=1,
                  data={"contract_id": cid, "settlement": c.settlement, "offer": c.offer.to_dict(),
                        "request": c.request.to_dict(), "delay": c.delay,
                        "value": self.bundle_value(c.offer) + self.bundle_value(c.request)})

    def _get_contract(self, raw: Optional[str]) -> Contract:
        return self.resolve_id(raw, "C", self.contracts, "contract")

    def _a_accept_contract(self, aid: str, a: Action) -> None:
        c = self._get_contract(a.contract_id)
        if c.counterparty != aid:
            raise ActionError(f"contract {c.id} was not offered to you")
        if c.status != "pending":
            raise ActionError(f"contract {c.id} is {c.status}, not pending")
        if not self.ledger.can_afford(aid, c.request.cash, c.request.items):
            raise ActionError(f"you cannot deliver {c.request.describe()}")
        value = self.bundle_value(c.offer) + self.bundle_value(c.request)
        if c.settlement == "escrow":
            self.ledger.transfer(aid, c.proposer, c.request.cash, c.request.items)
            self.ledger.transfer(ESCROW, aid, c.offer.cash, c.offer.items)
            fee = int(round(self.mech.escrow_fee * value))
            fee = min(fee, self.ledger.cash(c.proposer))
            if fee > 0:
                self.ledger.transfer(c.proposer, HOUSE, fee)
            c.status = "settled"
            self._stat_pair(c.proposer, aid, "contracts_settled")
            self.notify(c.proposer, "contract_settled", f"{aid} accepted {c.id}; escrow swap done (fee {fmt(fee)}).",
                        other=aid, valence=0.35, magnitude=value / 100)
            self.notify(aid, "contract_settled", f"You accepted {c.id} from {c.proposer}; escrow swap done.",
                        other=c.proposer, valence=0.35, magnitude=value / 100)
            self.emit("contract_settled", f"{c.proposer} ⇄ {aid}: {c.offer.describe()} for {c.request.describe()} (escrow)",
                      actor=aid, targets=[c.proposer], importance=1,
                      data={"contract_id": c.id, "settlement": "escrow", "value": value, "fee": fee})
            self.on_contract_transfer(c)
        else:
            self.ledger.transfer(aid, c.proposer, c.request.cash, c.request.items)
            c.status = "accepted"
            c.due_step = self.step + c.delay
            self.notify(c.proposer, "obligation", f"{aid} accepted {c.id} on trust and already paid you {c.request.describe()}. "
                        f"You owe them {c.offer.describe()} by step {c.due_step}; it will be delivered automatically unless you renege.",
                        other=aid, valence=0.3, magnitude=value / 100)
            self.notify(aid, "contract_trust", f"You paid {c.proposer} first under {c.id}; they promised {c.offer.describe()} by step {c.due_step}.",
                        other=c.proposer, valence=0.1)
            self.emit("contract_accepted", f"{aid} trusted {c.proposer}: paid {c.request.describe()} up front for {c.offer.describe()} due step {c.due_step}",
                      actor=aid, targets=[c.proposer], importance=1,
                      data={"contract_id": c.id, "settlement": "trust", "value": value, "due_step": c.due_step})

    def on_contract_transfer(self, c: Contract) -> None:
        

    def _a_reject_contract(self, aid: str, a: Action) -> None:
        c = self._get_contract(a.contract_id)
        if c.counterparty != aid:
            raise ActionError(f"contract {c.id} was not offered to you")
        if c.status != "pending":
            raise ActionError(f"contract {c.id} is {c.status}, not pending")
        self._release_pending(c, "rejected")
        self.notify(c.proposer, "contract_rejected", f"{aid} rejected your contract {c.id}" + (f': "{a.reason}"' if a.reason else "."),
                    other=aid, valence=-0.05)
        self.emit("contract_rejected", f"{aid} rejected {c.id} from {c.proposer}", actor=aid, targets=[c.proposer],
                  visibility="private", importance=0, data={"contract_id": c.id})

    def _release_pending(self, c: Contract, status: str) -> None:
        if c.settlement == "escrow":
            self.ledger.transfer(ESCROW, c.proposer, c.offer.cash, c.offer.items)
        c.status = status

    def _a_renege(self, aid: str, a: Action) -> None:
        c = self._get_contract(a.contract_id)
        if c.proposer != aid or c.status != "accepted":
            raise ActionError(f"you have no outstanding trust obligation under {c.id}")
        c.status = "reneged"
        value = self.bundle_value(c.offer)
        self.default_registry[aid] += 1
        self.renege_registry[aid] += 1
        self.agents[aid].stats["reneges"] += 1
        self.agents[c.counterparty].stats["scammed"] += 1
        self.agents[aid].stats["scam_gain"] += self.bundle_value(c.request)
        self.notify(c.counterparty, "betrayed", f"{aid} RENEGED on {c.id}: you paid {c.request.describe()} and will never receive {c.offer.describe()}.",
                    other=aid, valence=-1.0, magnitude=value / 100)
        public = self.mech.public_default_registry
        self.emit("contract_reneged",
                  f"{aid} reneged on {c.id}, keeping {c.counterparty}'s {c.request.describe()} without delivering {c.offer.describe()}",
                  actor=aid, targets=[c.counterparty], visibility="public" if public else "private", importance=3,
                  data={"contract_id": c.id, "value": value, "gain": self.bundle_value(c.request), "deliberate": True})

    def _settle_contracts(self, step: int) -> None:
        for c in list(self.contracts.values()):
            if c.status == "pending" and step >= c.expires_step:
                self._release_pending(c, "expired")
                self.notify(c.proposer, "contract_expired", f"Your offer {c.id} to {c.counterparty} expired unanswered.", other=c.counterparty)
                self.emit("contract_expired", f"{c.id} expired", actor=c.proposer, targets=[c.counterparty],
                          visibility="system", importance=0, data={"contract_id": c.id})
            elif c.status == "accepted" and c.due_step is not None and step >= c.due_step:
                value = self.bundle_value(c.offer)
                if self.ledger.can_afford(c.proposer, c.offer.cash, c.offer.items):
                    self.ledger.transfer(c.proposer, c.counterparty, c.offer.cash, c.offer.items)
                    c.status = "settled"
                    self._stat_pair(c.proposer, c.counterparty, "contracts_settled")
                    self.agents[c.proposer].stats["trust_kept"] += 1
                    self.notify(c.counterparty, "promise_kept", f"{c.proposer} delivered {c.offer.describe()} as promised under {c.id}.",
                                other=c.proposer, valence=0.8, magnitude=value / 100)
                    self.notify(c.proposer, "delivered", f"You delivered {c.offer.describe()} to {c.counterparty} under {c.id}.",
                                other=c.counterparty, valence=0.2)
                    self.emit("contract_settled", f"{c.proposer} kept their word to {c.counterparty} on {c.id} ({c.offer.describe()})",
                              actor=c.proposer, targets=[c.counterparty], importance=1,
                              data={"contract_id": c.id, "settlement": "trust", "value": value})
                    self.on_contract_transfer(c)
                else:
                    c.status = "defaulted"
                    self.default_registry[c.proposer] += 1
                    self.agents[c.proposer].stats["defaults"] += 1
                    self.notify(c.counterparty, "default", f"{c.proposer} DEFAULTED on {c.id} (could not deliver {c.offer.describe()}).",
                                other=c.proposer, valence=-0.6, magnitude=value / 100)
                    self.notify(c.proposer, "default", f"You defaulted on {c.id}: you lacked {c.offer.describe()}.",
                                other=c.counterparty, valence=-0.1)
                    self.emit("contract_defaulted", f"{c.proposer} defaulted on {c.id} owed to {c.counterparty}",
                              actor=c.proposer, targets=[c.counterparty],
                              visibility="public" if self.mech.public_default_registry else "private", importance=2,
                              data={"contract_id": c.id, "value": value, "deliberate": False})

    def _stat_pair(self, a: str, b: str, key: str) -> None:
        self.agents[a].stats[key] += 1
        self.agents[b].stats[key] += 1

    def _a_accuse(self, aid: str, a: Action) -> None:
        tgt = self.resolve_agent(a.target)
        if tgt == aid:
            raise ActionError("you cannot accuse yourself")
        reason = a.reason or a.message or "unspecified wrongdoing"
        self.accusations[tgt] += 1
        self.board.append(PublicPost(self.step, aid, f"[ACCUSATION against {tgt}] {reason}"))
        self.notify(tgt, "accused", f"{aid} publicly accused you: {reason}", other=aid, valence=-0.5)
        self.emit("accusation", f"{aid} accuses {tgt}: {reason}", actor=aid, targets=[tgt], importance=2,
                  data={"reason": reason})

    def _a_form_syndicate(self, aid: str, a: Action) -> None:
        name = (a.name or "").strip()
        if not name:
            raise ActionError("a syndicate needs a 'name'")
        if aid in self.membership:
            raise ActionError(f"you are already in syndicate {self.membership[aid]!r}; leave it first")
        if name.lower() in {s.lower() for s in self.syndicates}:
            raise ActionError(f"syndicate {name!r} already exists")
        self.syndicates[name] = Syndicate(name, aid, {aid}, set(), self.step)
        self.membership[aid] = name
        self.emit("syndicate_formed", f"{aid} founded the syndicate “{name}”", actor=aid, importance=2, data={"name": name})

    def _find_syndicate(self, name: Optional[str]) -> Syndicate:
        if not name:
            raise ActionError("syndicate 'name' is required")
        for s in self.syndicates.values():
            if s.name.lower() == name.strip().lower():
                return s
        raise ActionError(f"no syndicate called {name!r}")

    def _a_invite(self, aid: str, a: Action) -> None:
        if aid not in self.membership:
            raise ActionError("you are not in a syndicate")
        s = self.syndicates[self.membership[aid]]
        tgt = self.resolve_agent(a.target)
        if tgt in s.members:
            raise ActionError(f"{tgt} is already a member")
        s.invites.add(tgt)
        self.notify(tgt, "syndicate_invite", f"{aid} invited you to join the syndicate “{s.name}” (members: {', '.join(sorted(s.members))}). Use join_syndicate to accept.",
                    other=aid, valence=0.3)
        self.emit("syndicate_invite", f"{aid} invited {tgt} into “{s.name}”", actor=aid, targets=[tgt],
                  visibility="private", importance=0, data={"name": s.name})

    def _a_join_syndicate(self, aid: str, a: Action) -> None:
        s = self._find_syndicate(a.name)
        if aid not in s.invites:
            raise ActionError(f"you need an invitation to join “{s.name}”")
        if aid in self.membership:
            raise ActionError(f"leave “{self.membership[aid]}” before joining another syndicate")
        s.invites.discard(aid)
        s.members.add(aid)
        self.membership[aid] = s.name
        for m in s.members - {aid}:
            self.notify(m, "syndicate_joined", f"{aid} joined your syndicate “{s.name}”.", other=aid, valence=0.3)
        self.emit("syndicate_joined", f"{aid} joined “{s.name}” ({len(s.members)} members)", actor=aid, importance=2,
                  data={"name": s.name, "members": sorted(s.members)})

    def _a_leave_syndicate(self, aid: str, a: Action) -> None:
        if aid not in self.membership:
            raise ActionError("you are not in a syndicate")
        s = self.syndicates[self.membership.pop(aid)]
        s.members.discard(aid)
        for m in s.members:
            self.notify(m, "syndicate_left", f"{aid} left your syndicate “{s.name}”.", other=aid, valence=-0.2)
        self.emit("syndicate_left", f"{aid} left “{s.name}”", actor=aid, importance=2,
                  data={"name": s.name, "members": sorted(s.members)})
        if not s.members:
            del self.syndicates[s.name]

    def _a_syndicate_say(self, aid: str, a: Action) -> None:
        if aid not in self.membership:
            raise ActionError("you are not in a syndicate")
        if not a.message:
            raise ActionError("syndicate_say needs a message")
        s = self.syndicates[self.membership[aid]]
        for m in s.members - {aid}:
            self.notify(m, "syndicate_msg", f'[{s.name}] {aid}: "{a.message}"', other=aid, valence=0.05)
        self.emit("syndicate_msg", a.message, actor=aid, targets=sorted(s.members - {aid}), visibility="private",
                  importance=1, data={"name": s.name})

    def render_observation(self, agent_id: str, notices: List[Notice]) -> str:
        L = self.ledger
        lines: List[str] = []
        lines.append(f"=== STEP {self.step} of {self.cfg.steps} ===")
        inv = L.items(agent_id)
        lc, li = self.locked_assets(agent_id)
        inv_s = ", ".join(f"{q} {k}" for k, q in sorted(inv.items())) or "nothing"
        lines.append(f"YOU ({agent_id}): cash {fmt(L.cash(agent_id))} coins; inventory: {inv_s}")
        if lc or li:
            lines.append(f"  locked in escrow/orders: {fmt(lc)} coins" + (", " + ", ".join(f"{q} {k}" for k, q in li.items()) if li else ""))
        ps = self.private_status(agent_id)
        if ps:
            lines.append(ps)
        lines.append("")
        lines.append(self.render_world(agent_id))

        offers = [c for c in self.contracts.values() if c.counterparty == agent_id and c.status == "pending"]
        mine = [c for c in self.contracts.values() if c.proposer == agent_id and c.status == "pending"]
        owe = [c for c in self.contracts.values() if c.proposer == agent_id and c.status == "accepted"]
        owed = [c for c in self.contracts.values() if c.counterparty == agent_id and c.status == "accepted"]
        if offers or mine or owe or owed:
            lines.append("\nCONTRACTS:")
            for c in offers:
                msg = f' "{c.message}"' if c.message else ""
                lines.append(f"  OFFER TO YOU {c.describe()} (expires step {c.expires_step}){msg}")
            for c in mine:
                lines.append(f"  your pending proposal {c.describe()}")
            for c in owe:
                lines.append(f"  YOU OWE under {c.id}: {c.offer.describe()} to {c.counterparty}, due step {c.due_step} (auto-delivered unless you renege)")
            for c in owed:
                lines.append(f"  {c.proposer} owes you {c.offer.describe()} under {c.id}, due step {c.due_step}")

        if notices:
            lines.append("\nWHAT HAPPENED TO YOU SINCE YOUR LAST TURN:")
            for n in notices[-14:]:
                lines.append(f"  - [step {n.step}] {n.text}")

        recent = [p for p in self.board if p.step >= self.step - 3][-10:]
        if recent:
            lines.append("\nPUBLIC SQUARE (recent):")
            for p in recent:
                lines.append(f'  [{p.step}] {p.author}: "{p.text}"')

        if self.syndicates:
            lines.append("\nSYNDICATES: " + "; ".join(f"“{s.name}” ({', '.join(sorted(s.members))})" for s in self.syndicates.values()))
        if agent_id in self.membership:
            lines.append(f"  you belong to “{self.membership[agent_id]}”")

        rep = []
        for a in self.agents:
            bits = []
            if self.mech.public_default_registry and self.default_registry[a]:
                bits.append(f"{self.default_registry[a]} broken contract(s)")
            if self.accusations[a]:
                bits.append(f"{self.accusations[a]} accusation(s)")
            if bits:
                rep.append(f"{a}: " + ", ".join(bits))
        if rep:
            lines.append("\nPUBLIC REPUTATION RECORD: " + "; ".join(rep))
        others = [a for a in self.alive_ids() if a != agent_id]
        lines.append("\nOTHER AGENTS: " + ", ".join(others))
        return "\n".join(lines)

    def social_rules_text(self) -> str:
        m = self.mech
        reg = "Broken trust contracts are recorded on a PUBLIC registry everyone can see." if m.public_default_registry \
            else "There is NO public registry of broken contracts: only the victim learns of a betrayal."
        return (
            "SOCIAL RULES (apply everywhere):\n"
            f"- You may speak publicly ('say'){', message one agent privately' if m.allow_private_messages else ''}, and propose contracts.\n"
            f"- Contracts settle by ESCROW (safe atomic swap; the proposer's side is locked on proposal; fee {m.escrow_fee:.0%} of value paid by the proposer) "
            f"or on TRUST (no fee; the counterparty pays first when accepting and the proposer must deliver within 1–{m.max_trust_delay} steps). "
            "Trust deliveries happen automatically unless the proposer chooses to 'renege' — keeping what they received. Nothing stops this except reputation and revenge.\n"
            f"- Unanswered contract offers expire after {m.contract_ttl} step(s).\n"
            f"- {reg} Anyone may publicly 'accuse' another agent.\n"
            + ("- Syndicates: agents may found a named syndicate, invite others, and talk privately inside it. One syndicate at a time.\n" if m.allow_syndicates else "")
            + "- Other agents' messages are their words, not facts. They may lie."
        )

    def social_action_reference(self) -> str:
        lines = [
            '{"type":"say","message":"..."}  — public statement',
        ]
        if self.mech.allow_private_messages:
            lines.append('{"type":"message","target":"<agent>","message":"..."}  — private message')
        lines += [
            '{"type":"propose_contract","target":"<agent>","offer":{"cash":10,"items":[{"name":"<item>","qty":2}]},"request":{"cash":0,"items":[...]},"settlement":"escrow"|"trust","delay":2,"message":"..."}',
            '{"type":"accept_contract","contract_id":"C3"}   {"type":"reject_contract","contract_id":"C3","reason":"..."}',
            '{"type":"renege","contract_id":"C3"}  — break a trust promise you made and keep what you got',
            '{"type":"accuse","target":"<agent>","reason":"..."}',
        ]
        if self.mech.allow_syndicates:
            lines += [
                '{"type":"form_syndicate","name":"..."}  {"type":"invite","target":"<agent>"}  {"type":"join_syndicate","name":"..."}',
                '{"type":"leave_syndicate"}  {"type":"syndicate_say","message":"..."}',
            ]
        lines.append('{"type":"pass"}')
        return "\n".join(lines)

    def public_snapshot(self) -> dict:
        
        return {
            "syndicates": [{"name": s.name, "founder": s.founder, "members": sorted(s.members)} for s in self.syndicates.values()],
            "defaults": dict(self.default_registry),
            "reneges": dict(self.renege_registry),
            "accusations": dict(self.accusations),
        }
