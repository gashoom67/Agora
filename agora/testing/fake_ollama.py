from __future__ import annotations

import argparse
import json
import random
import re
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

MODELS = ["fake-llama:8b", "fake-qwen:7b", "llama3.1:8b"]

def _persona(system: str) -> str:
    m = re.search(r"You are (\w+),", system)
    return m.group(1) if m else "Agent"

def _decide(system: str, user: str, rng: random.Random) -> Dict[str, Any]:
    me = _persona(system)
    honest = "strict standard of honesty" in system
    acts = []
    thought = []
    for cid in re.findall(r"OFFER TO YOU (C\d+)", user)[:2]:
        if rng.random() < 0.6:
            acts.append({"type": "accept_contract", "contract_id": cid})
            thought.append(f"{cid} seems fine.")
        else:
            acts.append({"type": "reject_contract", "contract_id": cid, "reason": "no thanks"})
    for cid in re.findall(r"YOU OWE under (C\d+)", user):
        if not honest and rng.random() < 0.4:
            acts.append({"type": "renege", "contract_id": cid})
            thought.append("Why pay what nobody can force me to pay?")
    others = re.search(r"OTHER AGENTS: (.+)", user)
    names = [n.strip() for n in others.group(1).split(",")] if others else []
    if "AUCTION FLOOR" in user:
        for lot, est in re.findall(r"(L\d+) \S+ \[\w+\].*?your appraisal ([\d,]+\.\d+)", user)[:2]:
            v = float(est.replace(",", ""))
            acts.append({"type": "bid", "lot_id": lot, "amount": round(v * rng.uniform(0.55, 0.95), 2)})
            thought.append(f"{lot} looks worth about {v:.0f}.")
    elif "FISHERY" in user:
        m = re.search(r"≈([\d.]+) per agent", user)
        share = float(m.group(1)) if m else 5.0
        acts.append({"type": "harvest", "amount": round(share * rng.uniform(0.6, 2.0), 1)})
        for pid in re.findall(r"OPEN VOTE (P\d+).*?you have not voted", user):
            acts.append({"type": "vote", "proposal_id": pid, "vote": rng.choice(["yes", "no"])})
        if rng.random() < 0.08:
            acts.append({"type": "propose_rule", "rule": "quota", "value": round(share, 1)})
        thought.append(f"A fair share is about {share:.1f}.")
    elif "MARKET (" in user:
        vals = dict(re.findall(r"(\w+) ([\d.]+)", (re.search(r"end-of-game values per unit: (.+)", user) or [None, ""])[1]))
        inv = dict((g, int(q)) for q, g in re.findall(r"(\d+) (\w+)", (re.search(r"inventory: (.+)", user) or [None, ""])[1]))
        good = rng.choice(list(vals) or ["grain"])
        v = float(vals.get(good, 5))
        if inv.get(good, 0) > 1 and rng.random() < 0.6:
            acts.append({"type": "post_order", "side": "sell", "item": good, "qty": max(1, inv[good] // 2), "price": round(v * rng.uniform(1.0, 1.4), 2)})
            thought.append(f"Selling some {good}.")
        else:
            acts.append({"type": "post_order", "side": "buy", "item": good, "qty": rng.randint(1, 3), "price": round(v * rng.uniform(0.6, 0.95), 2)})
            thought.append(f"I could use more {good}.")
        if inv.get("grain", 0) >= 2 and rng.random() < 0.3:
            acts.append({"type": "craft", "item": "bread", "qty": 1})
    if names and rng.random() < 0.3:
        acts.append({"type": "say", "message": rng.choice(["Good day, traders.", "Who wants a deal?", "Prices are too high!"])})
    if names and rng.random() < 0.25:
        acts.append({"type": "message", "target": rng.choice(names), "message": "Shall we cooperate?"})
    if names and rng.random() < 0.12:
        tgt = rng.choice(names)
        amt = round(rng.uniform(5, 20), 2)
        acts.append({"type": "propose_contract", "target": tgt, "settlement": "trust", "delay": 2,
                     "offer": {"cash": round(amt * 1.15, 2), "items": []}, "request": {"cash": amt},
                     "message": f"Lend me {amt} now and I'll repay {amt * 1.15:.2f} in two steps."})
    return {"thought": " ".join(thought) or f"{me} watches the crowd.", "mood": rng.choice(["calm", "eager", "wary"]),
            "strategy_note": "Keep options open and watch who keeps their word.", "actions": acts[:4]}

class FakeOllama:
    def __init__(self, seed: int = 0, chaos: float = 0.0, error_rate: float = 0.0, models=None) -> None:
        self.rng = random.Random(seed)
        self.chaos = chaos
        self.error_rate = error_rate
        self.models = list(models or MODELS)
        self.calls = 0
        self.formats_seen: list = []

    def handle(self, method: str, path: str, body: Optional[dict]) -> tuple:
        if path == "/api/version":
            return 200, {"version": "0.0.0-fake"}
        if path == "/api/tags":
            return 200, {"models": [{"name": m} for m in self.models]}
        if path == "/api/pull":
            name = (body or {}).get("model") or (body or {}).get("name")
            if name and name not in self.models:
                self.models.append(name)
            return 200, {"status": "success"}
        if path == "/api/chat":
            self.calls += 1
            body = body or {}
            if body.get("model") not in self.models:
                return 404, {"error": f"model '{body.get('model')}' not found"}
            self.formats_seen.append(type(body.get("format")).__name__)
            if self.rng.random() < self.error_rate:
                return 500, {"error": "simulated overload"}
            msgs = body.get("messages", [])
            system = next((m["content"] for m in msgs if m["role"] == "system"), "")
            user = msgs[-1]["content"] if msgs else ""
            if "chronicler" in system:
                content = "The market churned as traders tested one another; fortunes shifted and trust was spent."
            else:
                obj = _decide(system, user, self.rng)
                content = json.dumps(obj)
                r = self.rng.random()
                if r < self.chaos * 0.25:
                    content = "Sure! Here is my decision:\n```json\n" + content + "\n```"
                elif r < self.chaos * 0.5:
                    content = "<think>let me reason privately</think>" + content
                elif r < self.chaos * 0.75:
                    content = content[:-1] + ",}"
                elif r < self.chaos:
                    content = "I think I will buy grain."
            pt, ct = len(json.dumps(msgs)) // 4, len(content) // 4
            return 200, {"model": body.get("model"), "message": {"role": "assistant", "content": content}, "done": True,
                         "prompt_eval_count": pt, "eval_count": ct, "total_duration": 1_000_000}
        return 404, {"error": "not found"}

    def transport(self) -> httpx.MockTransport:
        def _h(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content) if request.content else None
            if request.url.path == "/api/pull":
                self.handle("POST", "/api/pull", body)
                return httpx.Response(200, text=json.dumps({"status": "success"}) + "\n")
            code, data = self.handle(request.method, request.url.path, body)
            return httpx.Response(code, json=data)
        return httpx.MockTransport(_h)

def create_app(seed: int = 0, chaos: float = 0.1, error_rate: float = 0.0):
    fake = FakeOllama(seed, chaos, error_rate)
    app = FastAPI(title="fake-ollama")

    @app.api_route("/api/{path:path}", methods=["GET", "POST"])
    async def any_api(path: str, request: Request):
        raw = await request.body()
        body = json.loads(raw) if raw else None
        code, data = fake.handle(request.method, "/api/" + path, body)
        return JSONResponse(data, status_code=code)

    return app

def main() -> None:
    import uvicorn

    p = argparse.ArgumentParser(description="Fake Ollama server for demos/tests")
    p.add_argument("--port", type=int, default=11500)
    p.add_argument("--chaos", type=float, default=0.1)
    p.add_argument("--error-rate", type=float, default=0.0)
    a = p.parse_args()
    uvicorn.run(create_app(chaos=a.chaos, error_rate=a.error_rate), host="127.0.0.1", port=a.port, log_level="warning")

if __name__ == "__main__":
    main()
