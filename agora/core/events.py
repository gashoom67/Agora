from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

Visibility = Literal["public", "private", "system", "internal"]


@dataclass
class Event:
    kind: str
    step: int
    text: str = ""
    actor: Optional[str] = None
    targets: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    visibility: Visibility = "public"
    importance: int = 1
    id: int = 0
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


KINDS = {
    "sim_started", "sim_completed", "sim_failed", "sim_paused", "sim_resumed",
    "step_started", "step_completed",
    "thought", "say", "dm", "syndicate_msg",
    "order_posted", "order_cancelled", "order_expired", "trade",
    "production", "craft", "consumption", "hunger",
    "contract_proposed", "contract_accepted", "contract_rejected", "contract_expired",
    "contract_settled", "contract_reneged", "contract_defaulted",
    "accusation", "syndicate_formed", "syndicate_invite", "syndicate_joined", "syndicate_left",
    "lot_opened", "bid", "lot_closed", "lot_unsold", "appraisal", "shill_detected",
    "harvest", "regrowth", "restock", "rule_proposed", "vote", "rule_enacted", "rule_failed",
    "audit_fine", "dividend", "stock_collapse",
    "action_rejected", "llm_error", "metrics", "highlight", "summary",
}
