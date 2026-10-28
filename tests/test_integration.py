import json
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from agora.config import LLMConfig, SimulationConfig
from agora.engine.runner import SimulationRunner
from agora.llm.ollama import LLMError, OllamaClient
from agora.server.app import create_app
from agora.testing.fake_ollama import FakeOllama


async def test_client_retries_then_succeeds():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, text="busy")
        return httpx.Response(200, json={"message": {"content": "<think>hmm</think>{\"a\":1}"}, "prompt_eval_count": 3, "eval_count": 2})

    c = OllamaClient(max_retries=3, transport=httpx.MockTransport(handler))

    r = await c.chat("m", [{"role": "user", "content": "hi"}], schema={"type": "object"})
    assert r.content == '{"a":1}' and c.stats.retries == 2 and c.stats.calls == 1
    await c.aclose()


async def test_client_schema_downgrade_and_404():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body.get("format"))
        if body["model"] == "missing":
            return httpx.Response(404, json={"error": "model not found"})
        if isinstance(body.get("format"), dict):
            return httpx.Response(400, json={"error": "invalid format"})
        return httpx.Response(200, json={"message": {"content": "{}"}})

    c = OllamaClient(max_retries=2, transport=httpx.MockTransport(handler))
    await c.chat("m", [{"role": "user", "content": "x"}], schema={"type": "object"})
    assert seen[:2] == [{"type": "object"}, "json"] and c.schema_supported is False
    with pytest.raises(LLMError) as ei:
        await c.chat("missing", [{"role": "user", "content": "x"}])
    assert not ei.value.retryable
    await c.aclose()


async def test_client_health_unreachable():
    def handler(req):
        raise httpx.ConnectError("refused")

    async with OllamaClient(transport=httpx.MockTransport(handler)) as c:
        h = await c.health()
    assert h["ok"] is False and "cannot reach" in h["error"]


@pytest.mark.parametrize("env", ["marketplace", "auction_house", "commons"])
async def test_full_heuristic_run_twenty_agents(env, tmp_path):
    cfg = SimulationConfig(environment=env, num_agents=20, steps=25, seed=11, output_dir=str(tmp_path),
                           llm=LLMConfig(backend="heuristic"), summary_every=5)
    r = SimulationRunner(cfg)
    s = await r.run()
    assert s["status"] == "completed" and s["error"] is None and s["step"] == 25
    r.env.ledger.check_invariants()
    d = r.run_dir
    for f in ("events.jsonl", "metrics.csv", "agents.csv", "report.html", "report.md", "network.graphml",
              "highlights.json", "chronicle.md", "summary.json", "config.json", "personas.json"):
        assert os.path.isfile(os.path.join(d, f)), f
    assert os.path.isfile(s["report"]["bundle"])
    assert len(os.listdir(os.path.join(d, "charts"))) >= 6
    assert len(r.chronicle) == 5
    snap = r.snapshot()
    assert len(snap["agents"]) == 20 and snap["metrics"] and snap["network"]["nodes"]


async def test_runs_are_reproducible(tmp_path):
    def cfg(sub):
        return SimulationConfig(environment="marketplace", num_agents=8, steps=10, seed=42, output_dir=str(tmp_path / sub),
                                llm=LLMConfig(backend="heuristic"))
    a = SimulationRunner(cfg("a"))
    b = SimulationRunner(cfg("b"))
    await a.run()
    await b.run()
    strip = lambda rows: [{k: v for k, v in r.items()} for r in rows]
    assert strip(a.tracker.rows) == strip(b.tracker.rows)


async def test_llm_pipeline_with_fake_ollama(tmp_path):
    fake = FakeOllama(seed=3, chaos=0.4, error_rate=0.05)
    cfg = SimulationConfig(environment="marketplace", num_agents=10, steps=6, seed=2, output_dir=str(tmp_path),
                           llm=LLMConfig(backend="ollama", model="fake-llama:8b", model_pool=["fake-llama:8b", "fake-qwen:7b"],
                                         max_concurrency=5, max_retries=3))
    r = SimulationRunner(cfg, transport=fake.transport())
    kinds = []
    r.bus.subscribe(lambda e: kinds.append(e.kind))
    s = await r.run()
    assert s["status"] == "completed", s
    assert kinds.count("thought") >= 50 
    assert "dict" in fake.formats_seen 
    assert r.llm_stats()["calls"] >= 60
    assert os.path.isfile(os.path.join(r.run_dir, "prompts", f"system_{r.personas[0].id}.txt"))
    assert {p.model for p in r.personas} == {"fake-llama:8b", "fake-qwen:7b"}


async def test_missing_model_fails_cleanly(tmp_path):
    fake = FakeOllama(models=["other:1b"])
    cfg = SimulationConfig(num_agents=3, steps=2, output_dir=str(tmp_path), llm=LLMConfig(backend="ollama", model="nope:7b"))
    r = SimulationRunner(cfg, transport=fake.transport())
    s = await r.run()
    assert s["status"] == "failed" and "missing model" in s["error"]


async def test_auto_pull(tmp_path):
    fake = FakeOllama(models=[])
    cfg = SimulationConfig(num_agents=3, steps=2, output_dir=str(tmp_path), llm=LLMConfig(backend="ollama", model="fresh:1b", auto_pull=True))
    r = SimulationRunner(cfg, transport=fake.transport())
    s = await r.run()
    assert s["status"] == "completed" and "fresh:1b" in fake.models


async def test_pause_resume_stop(tmp_path):
    import asyncio

    cfg = SimulationConfig(num_agents=4, steps=500, output_dir=str(tmp_path), step_delay_s=0.01, llm=LLMConfig(backend="heuristic"))
    r = SimulationRunner(cfg)
    task = asyncio.create_task(r.run())
    while r.step < 3:
        await asyncio.sleep(0.01)
    r.pause()
    await asyncio.sleep(0.1)
    frozen = r.step
    await asyncio.sleep(0.1)
    assert r.step == frozen and r.status == "paused"
    r.resume()
    await asyncio.sleep(0.05)
    r.stop()
    s = await task
    assert s["status"] == "stopped" and s["step"] < 500 and s["report"]


def test_server_end_to_end(tmp_path):
    app = create_app(str(tmp_path))
    with TestClient(app) as client:
        meta = client.get("/api/meta").json()
        assert len(meta["environments"]) == 3 and len(meta["archetypes"]) == 8 and meta["max_agents"] == 20
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/sim/state").json()["status"] == "idle"
        bad = client.post("/api/sim/start", json={"num_agents": 50})
        assert bad.status_code == 422
        bad = client.post("/api/sim/start", json={"archetype_mix": {"wizard": 1}})
        assert bad.status_code == 422
        with client.websocket_connect("/ws") as ws:
            first = json.loads(ws.receive_text())
            assert first["type"] == "snapshot"
            r = client.post("/api/sim/start", json={"environment": "commons", "num_agents": 6, "steps": 6,
                                                     "llm": {"backend": "heuristic"}, "name": "server test"})
            assert r.status_code == 200
            run_id = r.json()["run_id"]
            assert client.post("/api/sim/start", json={"llm": {"backend": "heuristic"}}).status_code == 409
            kinds = set()
            deadline = time.time() + 60
            while time.time() < deadline:
                msg = json.loads(ws.receive_text())
                if msg["type"] == "event":
                    kinds.add(msg["event"]["kind"])
                    if msg["event"]["kind"] == "sim_completed":
                        break
            assert {"sim_started", "thought", "metrics", "step_completed", "sim_completed"} <= kinds
        state = client.get("/api/sim/state").json()
        assert state["status"] == "completed" and len(state["agents"]) == 6
        runs = client.get("/api/runs").json()["runs"]
        assert runs and runs[0]["run_id"] == run_id
        rep = client.get(f"/api/runs/{run_id}/report")
        assert rep.status_code == 200 and "<html" in rep.text
        assert client.get(f"/api/runs/{run_id}/bundle").headers["content-type"] == "application/zip"
        assert client.get(f"/api/runs/{run_id}/files/metrics.csv").status_code == 200
        assert client.get(f"/api/runs/{run_id}/files/secret.txt").status_code == 404
        assert client.get("/api/runs/..%2F..%2Fetc/report").status_code in (400, 404)
