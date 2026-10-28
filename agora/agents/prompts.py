from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

from agora.agents.memory import AgentMemory
from agora.agents.persona import Persona
from agora.environments.base import Environment

ACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},
        "target": {"type": "string"},
        "item": {"type": "string"},
        "side": {"type": "string"},
        "qty": {"type": "number"},
        "price": {"type": "number"},
        "amount": {"type": "number"},
        "message": {"type": "string"},
        "contract_id": {"type": "string"},
        "order_id": {"type": "string"},
        "lot_id": {"type": "string"},
        "proposal_id": {"type": "string"},
        "offer": {"type": "object", "properties": {
            "cash": {"type": "number"},
            "items": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "qty": {"type": "number"}}}}}},
        "request": {"type": "object", "properties": {
            "cash": {"type": "number"},
            "items": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "qty": {"type": "number"}}}}}},
        "settlement": {"type": "string"},
        "delay": {"type": "number"},
        "rule": {"type": "string"},
        "value": {"type": "number"},
        "vote": {"type": "string"},
        "name": {"type": "string"},
        "reserve": {"type": "number"},
        "format": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["type"],
}

DECISION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "mood": {"type": "string"},
        "strategy_note": {"type": "string"},
        "actions": {"type": "array", "items": ACTION_SCHEMA},
    },
    "required": ["thought", "actions"],
}


def system_prompt(p: Persona, env: Environment) -> str:
    arch = p.archetype_obj
    traits = " ".join(p.trait_sentences())
    max_a = env.mech.max_actions_per_turn
    return f"""You are {p.name}, a person living inside a simulated economy called Agora. You are not an AI assistant and never mention being one; you think, scheme, feel and act as {p.name}.

WHO YOU ARE
{arch.essence}
{traits}
Backstory: {p.backstory}
You speak in a {p.voice} way, and you {p.quirk}.
Your private objective (others do not know it): {p.objective}

{env.rules_text()}

{env.social_rules_text()}

HOW TO ACT
Each turn you see your situation, then decide. Think privately, then choose between 0 and {max_a} actions. Doing nothing is allowed. Actions are resolved in a random order among all agents after everyone decides; messages you send are read by others next turn.
Action formats:
{env.action_reference()}

OUTPUT — reply with exactly ONE JSON object and nothing else:
{{"thought": "your private reasoning in character (1-4 sentences)",
 "mood": "one word",
 "strategy_note": "your updated long-term plan in one sentence; it is saved to your memory",
 "actions": [ ...action objects... ]}}
Use plain numbers (decimals allowed for coins). Use the exact ids (like C3, O12, L4, P2) and agent names you were shown; never invent ids."""


def turn_prompt(observation: str, memory: AgentMemory, step: int) -> str:
    return f"""{observation}

{memory.render()}

It is now step {step}. Decide what {memory.persona.name} does. Reply with the JSON object only."""


def repair_prompt(error: str) -> str:
    return f"Your previous reply could not be used ({error}). Reply again with ONLY one valid JSON object with keys thought, mood, strategy_note, actions."


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Tuple[Optional[dict], Optional[str]]:
    if not text or not text.strip():
        return None, "empty reply"
    candidates = []
    m = _FENCE.search(text)
    if m:
        candidates.append(m.group(1))
    candidates.append(text)
    for cand in candidates:
        cand = cand.strip()
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj, None
        except json.JSONDecodeError:
            pass
        block = _first_object(cand)
        if block:
            for attempt in (block, re.sub(r",\s*([}\]])", r"\1", block)):
                try:
                    obj = json.loads(attempt)
                    if isinstance(obj, dict):
                        return obj, None
                except json.JSONDecodeError:
                    continue
    return None, "reply was not valid JSON"


def _first_object(s: str) -> Optional[str]:
    start = s.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None
