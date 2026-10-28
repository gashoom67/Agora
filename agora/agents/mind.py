from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from agora.agents.heuristic import HeuristicPolicy
from agora.agents.memory import AgentMemory
from agora.agents.persona import Persona
from agora.agents.prompts import DECISION_SCHEMA, extract_json, repair_prompt, system_prompt, turn_prompt
from agora.config import LLMConfig
from agora.core.actions import Action, normalize_actions
from agora.environments.base import Environment
from agora.llm.ollama import LLMError, OllamaClient


@dataclass
class Decision:
    agent: str
    step: int
    thought: str = ""
    mood: str = ""
    strategy_note: str = ""
    actions: List[Action] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    source: str = "llm"
    latency_s: float = 0.0
    tokens: int = 0
    raw: str = ""


class AgentMind:
    def __init__(self, persona: Persona, memory: AgentMemory, backend: str, llm_cfg: LLMConfig,
                 client: Optional[OllamaClient], seed: int) -> None:
        self.persona = persona
        self.memory = memory
        self.backend = backend
        self.cfg = llm_cfg
        self.client = client
        self.seed = seed
        self._system: Optional[str] = None
        self.last_prompt: str = ""

    def _apply(self, d: Decision, obj: dict, env: Environment) -> Decision:
        d.thought = str(obj.get("thought") or "")[:600]
        d.mood = str(obj.get("mood") or "")[:24]
        d.strategy_note = str(obj.get("strategy_note") or "")[:240]
        acts, errs = normalize_actions(obj.get("actions"), env.mech.max_actions_per_turn, env.mech.message_char_limit)
        d.actions, d.errors = acts, errs
        self.memory.record_thought(env.step, d.thought)
        if d.strategy_note:
            self.memory.strategy = d.strategy_note
        if d.mood:
            self.memory.mood = d.mood
        return d

    async def decide(self, env: Environment, observation: str) -> Decision:
        d = Decision(agent=self.persona.id, step=env.step, source=self.backend)
        t0 = time.perf_counter()
        if self.backend == "heuristic":
            policy = HeuristicPolicy(self.persona, self.memory, seed=self.seed * 1000 + env.step)
            obj = policy.decide(env)
            self._apply(d, obj, env)
            d.latency_s = time.perf_counter() - t0
            return d

        assert self.client is not None
        if self._system is None:
            self._system = system_prompt(self.persona, env)
        user = turn_prompt(observation, self.memory, env.step)
        self.last_prompt = user
        messages = [{"role": "system", "content": self._system}, {"role": "user", "content": user}]
        model = self.persona.model or self.cfg.model
        try:
            resp = await self.client.chat(model, messages, schema=DECISION_SCHEMA, temperature=self.persona.temperature,
                                          seed=self.seed * 100003 + env.step)
            d.tokens += resp.prompt_tokens + resp.completion_tokens
            d.raw = resp.content
            obj, err = extract_json(resp.content)
            if obj is None:
                messages += [{"role": "assistant", "content": resp.content[:2000]}, {"role": "user", "content": repair_prompt(err or "invalid")}]
                resp = await self.client.chat(model, messages, schema=DECISION_SCHEMA, temperature=max(0.2, self.persona.temperature * 0.6))
                d.tokens += resp.prompt_tokens + resp.completion_tokens
                d.raw = resp.content
                obj, err = extract_json(resp.content)
            if obj is None:
                d.source = "error"
                d.errors = [f"unusable model output: {err}"]
            else:
                self._apply(d, obj, env)
        except LLMError as e:
            d.source = "error"
            d.errors = [str(e)]
        d.latency_s = time.perf_counter() - t0
        return d
