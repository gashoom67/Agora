from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from agora.core.events import Event

def gini(values: Sequence[float]) -> float:
    
    x = np.clip(np.asarray(values, dtype=float), 0, None)
    n = x.size
    if n == 0 or x.sum() <= 0:
        return 0.0
    x = np.sort(x)
    i = np.arange(1, n + 1)
    return float((2 * np.sum(i * x)) / (n * x.sum()) - (n + 1) / n)

def theil(values: Sequence[float]) -> float:
    
    x = np.clip(np.asarray(values, dtype=float), 0, None)
    if x.size == 0 or x.sum() <= 0:
        return 0.0
    r = x / x.mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(r > 0, r * np.log(r), 0.0)
    return float(t.mean())

def top_share(values: Sequence[float], frac: float = 0.2) -> float:
    x = np.sort(np.clip(np.asarray(values, dtype=float), 0, None))[::-1]
    if x.size == 0 or x.sum() <= 0:
        return 0.0
    k = max(1, int(math.ceil(frac * x.size)))
    return float(x[:k].sum() / x.sum())

def lorenz(values: Sequence[float]) -> Tuple[List[float], List[float]]:
    x = np.sort(np.clip(np.asarray(values, dtype=float), 0, None))
    n = x.size
    if n == 0 or x.sum() <= 0:
        return [0.0, 1.0], [0.0, 1.0]
    cum = np.concatenate([[0.0], np.cumsum(x) / x.sum()])
    pop = np.linspace(0, 1, n + 1)
    return pop.tolist(), cum.tolist()

def hhi(shares: Sequence[float]) -> float:
    
    s = np.asarray(shares, dtype=float)
    tot = s.sum()
    if tot <= 0:
        return 0.0
    s = s / tot
    return float(np.sum(s ** 2))

def log_return_volatility(prices: Sequence[float]) -> float:
    p = np.asarray([v for v in prices if v and v > 0], dtype=float)
    if p.size < 3:
        return 0.0
    r = np.diff(np.log(p))
    return float(r.std(ddof=1))

COUNTED = (
    "trade", "say", "dm", "syndicate_msg", "contract_proposed", "contract_settled", "contract_accepted",
    "contract_reneged", "contract_defaulted", "accusation", "action_rejected", "llm_error", "bid", "lot_closed",
    "rule_proposed", "rule_enacted", "audit_fine", "shill_detected", "craft", "hunger",
)

class MetricsTracker:
    

    def __init__(self) -> None:
        self._counts: Counter = Counter()
        self._trade_value = 0
        self.rows: List[dict] = []
        self.worth_history: Dict[str, List[float]] = defaultdict(list)
        self.cumulative: Counter = Counter()

    def on_event(self, ev: Event) -> None:
        if ev.kind in COUNTED:
            self._counts[ev.kind] += 1
            self.cumulative[ev.kind] += 1
        if ev.kind == "trade":
            self._trade_value += int(ev.data.get("value", 0))
        if ev.kind == "contract_settled":
            self._trade_value += int(ev.data.get("value", 0))

    def close_step(self, step: int, env) -> dict:
        worths = {a: env.net_worth(a) / 100 for a in env.agents}
        for a, w in worths.items():
            self.worth_history[a].append(w)
        vals = list(worths.values())
        row = {
            "step": step,
            "gini": round(gini(vals), 4),
            "theil": round(theil(vals), 4),
            "top20_share": round(top_share(vals, 0.2), 4),
            "mean_worth": round(float(np.mean(vals)), 2) if vals else 0.0,
            "median_worth": round(float(np.median(vals)), 2) if vals else 0.0,
            "min_worth": round(min(vals), 2) if vals else 0.0,
            "max_worth": round(max(vals), 2) if vals else 0.0,
            "trades": self._counts["trade"],
            "trade_value": round(self._trade_value / 100, 2),
            "public_msgs": self._counts["say"],
            "private_msgs": self._counts["dm"] + self._counts["syndicate_msg"],
            "contracts_proposed": self._counts["contract_proposed"],
            "contracts_settled": self._counts["contract_settled"],
            "reneges": self._counts["contract_reneged"],
            "defaults": self._counts["contract_defaulted"],
            "accusations": self._counts["accusation"],
            "rejected_actions": self._counts["action_rejected"],
            "llm_errors": self._counts["llm_error"],
            "syndicates": len(env.syndicates),
            "house_cash": round(env.ledger.cash("__house__") / 100, 2),
        }
        env_m = env.env_metrics()
        row["env"] = env_m
        self.rows.append(row)
        self._counts = Counter()
        self._trade_value = 0
        return row

    def flat_rows(self) -> List[dict]:
        
        out = []
        for r in self.rows:
            flat = {k: v for k, v in r.items() if k != "env"}
            for k, v in (r.get("env") or {}).items():
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        if isinstance(v2, (int, float, str, bool)) or v2 is None:
                            flat[f"{k}.{k2}"] = v2
                        elif isinstance(v2, dict):
                            for k3, v3 in v2.items():
                                if isinstance(v3, (int, float, str, bool)) or v3 is None:
                                    flat[f"{k}.{k2}.{k3}"] = v3
                elif isinstance(v, (int, float, str, bool)) or v is None:
                    flat[k] = v
            out.append(flat)
        return out

    def last(self) -> Optional[dict]:
        return self.rows[-1] if self.rows else None
