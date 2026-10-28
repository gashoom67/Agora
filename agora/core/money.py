from __future__ import annotations

import math
from typing import Any

CENTS = 100

def to_cents(x: Any) -> int:
    
    if isinstance(x, bool):
        raise ValueError("boolean is not an amount")
    if isinstance(x, str):
        x = x.strip().replace(",", "").lstrip("$")
    v = float(x)
    if not math.isfinite(v):
        raise ValueError("amount must be finite")
    return int(round(v * CENTS))

def from_cents(c: int) -> float:
    return round(c / CENTS, 2)

def fmt(c: int) -> str:
    return f"{c / CENTS:,.2f}"
