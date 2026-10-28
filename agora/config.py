from __future__ import annotations

import time
import uuid
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

EnvironmentName = Literal["marketplace", "auction_house", "commons"]
AuctionFormat = Literal["rotate", "first_price", "vickrey", "english"]

MAX_AGENTS = 20

class LLMConfig(BaseModel):

    backend: Literal["ollama", "heuristic"] = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    model_pool: List[str] = Field(default_factory=list)
    narrator_model: Optional[str] = None
    max_concurrency: int = Field(4, ge=1, le=20)
    timeout_s: float = Field(180.0, gt=0)
    max_retries: int = Field(2, ge=0, le=6)
    temperature: float = Field(0.8, ge=0.0, le=2.0)
    num_ctx: int = Field(8192, ge=1024, le=131072)
    num_predict: int = Field(700, ge=64, le=4096)
    keep_alive: str = "30m"
    auto_pull: bool = False
    use_json_schema: bool = True

    def model_for(self, index: int) -> str:
        if self.model_pool:
            return self.model_pool[index % len(self.model_pool)]
        return self.model

class MechanismConfig(BaseModel):
    escrow_fee: float = Field(0.02, ge=0.0, le=0.5)
    public_default_registry: bool = True
    max_trust_delay: int = Field(5, ge=1, le=50)
    contract_ttl: int = Field(2, ge=1, le=10)
    message_char_limit: int = Field(280, ge=40, le=2000)
    max_actions_per_turn: int = Field(4, ge=1, le=10)
    allow_private_messages: bool = True
    allow_syndicates: bool = True

class MarketplaceConfig(BaseModel):
    trade_fee: float = Field(0.01, ge=0.0, le=0.3)
    production_per_step: int = Field(3, ge=0, le=50)
    order_ttl: int = Field(5, ge=1, le=100)
    subsistence: bool = True
    hunger_penalty: float = Field(4.0, ge=0.0)
    craft_cost: float = Field(1.0, ge=0.0)
    starting_cash: float = Field(200.0, ge=0.0)
    starting_units: int = Field(4, ge=0)

class AuctionConfig(BaseModel):
    format: AuctionFormat = "rotate"
    lots_per_step: int = Field(1, ge=1, le=5)
    max_open_lots: int = Field(3, ge=1, le=10)
    reserve_fraction: float = Field(0.4, ge=0.0, le=2.0)
    signal_noise: float = Field(0.25, ge=0.0, le=2.0)
    english_increment: float = Field(5.0, gt=0.0)
    english_max_rounds: int = Field(4, ge=1, le=20)
    stipend: float = Field(12.0, ge=0.0)
    starting_cash: float = Field(400.0, ge=0.0)
    allow_consignment: bool = True
    shill_detection_prob: float = Field(0.5, ge=0.0, le=1.0)
    shill_fine_multiplier: float = Field(2.0, ge=0.0)
    house_fee: float = Field(0.05, ge=0.0, le=0.5)
    public_appraisal: bool = False

class CommonsConfig(BaseModel):
    capacity: float = Field(1000.0, gt=0)
    initial_fraction: float = Field(0.8, gt=0.0, le=1.0)
    growth_rate: float = Field(0.35, gt=0.0, le=2.0)
    base_price: float = Field(10.0, gt=0)
    price_slope: float = Field(0.02, ge=0.0)
    min_price: float = Field(2.0, ge=0.0)
    harvest_cost: float = Field(2.0, ge=0.0)
    max_harvest_per_agent: float = Field(60.0, gt=0)
    upkeep: float = Field(8.0, ge=0.0)
    monitoring_prob: float = Field(0.3, ge=0.0, le=1.0)
    monitoring_cost: float = Field(1.0, ge=0.0)
    fine_multiplier: float = Field(3.0, ge=0.0)
    collapse_fraction: float = Field(0.05, ge=0.0, lt=1.0)
    restock_cost: float = Field(6.0, gt=0)
    vote_window: int = Field(1, ge=1, le=5)
    quorum: float = Field(0.34, ge=0.0, le=1.0)
    starting_cash: float = Field(100.0, ge=0.0)

class SimulationConfig(BaseModel):
    run_id: str = Field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    name: str = "untitled experiment"
    environment: EnvironmentName = "marketplace"
    num_agents: int = Field(12, ge=2, le=MAX_AGENTS)
    steps: int = Field(40, ge=1, le=2000)
    seed: int = 7
    archetype_mix: Dict[str, float] = Field(default_factory=dict)
    step_delay_s: float = Field(0.0, ge=0.0, le=30.0)
    summary_every: int = Field(5, ge=1, le=100)
    output_dir: str = "runs"
    llm: LLMConfig = Field(default_factory=LLMConfig)
    mechanism: MechanismConfig = Field(default_factory=MechanismConfig)
    marketplace: MarketplaceConfig = Field(default_factory=MarketplaceConfig)
    auction: AuctionConfig = Field(default_factory=AuctionConfig)
    commons: CommonsConfig = Field(default_factory=CommonsConfig)

    @field_validator("archetype_mix")
    @classmethod
    def _non_negative(cls, v: Dict[str, float]) -> Dict[str, float]:
        for k, w in v.items():
            if w < 0:
                raise ValueError(f"archetype weight for {k!r} must be >= 0")
        return v

    @model_validator(mode="after")
    def _check_mix(self) -> "SimulationConfig":
        from agora.agents.persona import ARCHETYPES

        unknown = set(self.archetype_mix) - set(ARCHETYPES)
        if unknown:
            raise ValueError(f"unknown archetypes: {sorted(unknown)}")
        if self.archetype_mix and sum(self.archetype_mix.values()) <= 0:
            raise ValueError("archetype_mix must have a positive total weight")
        return self
