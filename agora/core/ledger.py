from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional

WORLD = "__world__"     
HOUSE = "__house__"     
ESCROW = "__escrow__"   
SYSTEM_ACCOUNTS = (WORLD, HOUSE, ESCROW)

class InsufficientFunds(Exception):
    

@dataclass
class Account:
    cash: int = 0
    items: Counter = field(default_factory=Counter)

    def snapshot(self) -> dict:
        return {"cash": self.cash, "items": {k: v for k, v in self.items.items() if v}}

class Ledger:
    def __init__(self) -> None:
        self.accounts: Dict[str, Account] = {a: Account() for a in SYSTEM_ACCOUNTS}
        self.journal_entries = 0
        self.volume_cash = 0  

    
    def open(self, account_id: str) -> Account:
        if account_id not in self.accounts:
            self.accounts[account_id] = Account()
        return self.accounts[account_id]

    def account(self, account_id: str) -> Account:
        try:
            return self.accounts[account_id]
        except KeyError:
            raise KeyError(f"unknown account {account_id!r}") from None

    def cash(self, account_id: str) -> int:
        return self.account(account_id).cash

    def qty(self, account_id: str, item: str) -> int:
        return self.account(account_id).items.get(item, 0)

    def items(self, account_id: str) -> Dict[str, int]:
        return {k: v for k, v in self.account(account_id).items.items() if v > 0}

    
    def can_afford(self, account_id: str, cash: int = 0, items: Optional[Mapping[str, int]] = None) -> bool:
        acc = self.account(account_id)
        if account_id == WORLD:
            return True
        if cash > acc.cash:
            return False
        for it, q in (items or {}).items():
            if q > acc.items.get(it, 0):
                return False
        return True

    
    def transfer(
        self,
        src: str,
        dst: str,
        cash: int = 0,
        items: Optional[Mapping[str, int]] = None,
    ) -> None:
        
        items = {k: int(v) for k, v in (items or {}).items() if int(v) != 0}
        if cash < 0 or any(v < 0 for v in items.values()):
            raise ValueError("transfer amounts must be non-negative")
        if src == dst:
            return
        if not self.can_afford(src, cash, items):
            raise InsufficientFunds(f"{src} cannot cover cash={cash} items={items}")
        s, d = self.account(src), self.account(dst)
        s.cash -= cash
        d.cash += cash
        for it, q in items.items():
            s.items[it] -= q
            d.items[it] += q
            if s.items[it] == 0:
                del s.items[it]
        self.journal_entries += 1
        if src not in SYSTEM_ACCOUNTS and dst not in SYSTEM_ACCOUNTS:
            self.volume_cash += cash

    def mint(self, dst: str, cash: int = 0, items: Optional[Mapping[str, int]] = None) -> None:
        self.transfer(WORLD, dst, cash, items)

    def burn(self, src: str, cash: int = 0, items: Optional[Mapping[str, int]] = None) -> None:
        self.transfer(src, WORLD, cash, items)

    
    def check_invariants(self) -> None:
        total_cash = sum(a.cash for a in self.accounts.values())
        if total_cash != 0:
            raise AssertionError(f"cash not conserved: residual {total_cash}")
        totals: Counter = Counter()
        for a in self.accounts.values():
            totals.update(a.items)
        
        bad = {k: v for k, v in totals.items() if v != 0}
        if bad:
            raise AssertionError(f"items not conserved: {bad}")
        for aid, a in self.accounts.items():
            if aid == WORLD:
                continue
            if a.cash < 0:
                raise AssertionError(f"negative cash in {aid}: {a.cash}")
            neg = {k: v for k, v in a.items.items() if v < 0}
            if neg:
                raise AssertionError(f"negative items in {aid}: {neg}")

    def circulating_supply(self, item: str, holders: Iterable[str]) -> int:
        return sum(self.qty(h, item) for h in holders)
