from __future__ import annotations

import random
from typing import Dict, List, Type

from agora.agents.persona import Persona
from agora.config import SimulationConfig
from agora.environments.auction import AuctionHouse
from agora.environments.base import Environment
from agora.environments.commons import Commons
from agora.environments.marketplace import Marketplace

ENVIRONMENTS: Dict[str, Type[Environment]] = {
    Marketplace.key: Marketplace,
    AuctionHouse.key: AuctionHouse,
    Commons.key: Commons,
}

def build_environment(cfg: SimulationConfig, personas: List[Persona], rng: random.Random) -> Environment:
    env = ENVIRONMENTS[cfg.environment](cfg, personas, rng)
    env.setup()
    env.ledger.check_invariants()
    return env

def describe_environments() -> List[dict]:
    return [{"key": k, "title": c.title, "blurb": c.blurb} for k, c in ENVIRONMENTS.items()]
