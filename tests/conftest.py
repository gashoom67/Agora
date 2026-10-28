import random

import pytest

from agora.agents.persona import generate_personas
from agora.config import LLMConfig, SimulationConfig
from agora.environments.registry import build_environment


def make_env(env: str, n: int = 4, seed: int = 1, **overrides):
    cfg = SimulationConfig(environment=env, num_agents=n, seed=seed, llm=LLMConfig(backend="heuristic"), **overrides)
    personas = generate_personas(n, env, seed)
    return build_environment(cfg, personas, random.Random(seed)), [p.id for p in personas]


@pytest.fixture
def market():
    return make_env("marketplace")


@pytest.fixture
def auction():
    return make_env("auction_house")


@pytest.fixture
def commons():
    return make_env("commons")
