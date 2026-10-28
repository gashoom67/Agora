from __future__ import annotations

import asyncio
import logging
import os
import random
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

import httpx

from agora.agents.memory import AgentMemory
from agora.agents.mind import AgentMind, Decision
from agora.agents.persona import ARCHETYPES, generate_personas
from agora.agents.prompts import system_prompt, turn_prompt
from agora.config import SimulationConfig
from agora.core.actions import ActionError
from agora.core.events import Event
from agora.environments.registry import build_environment
from agora.llm.ollama import OllamaClient
from agora.observe.bus import EventBus
from agora.observe.chronicle import facts_for_window, narrate
from agora.observe.detectors import DetectorSuite, Highlight
from agora.observe.metrics import MetricsTracker
from agora.observe.network import SocialGraph
from agora.observe.recorder import Recorder
from agora.observe.report import ReportBuilder

log = logging.getLogger("agora.runner")

class SimulationRunner:
    def __init__(self, cfg: SimulationConfig, bus: Optional[EventBus] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.cfg = cfg
        self.bus = bus or EventBus()
        self.transport = transport
        self.status = "created"
        self.error: Optional[str] = None
        self.step = 0
        self._gate = asyncio.Event()
        self._gate.set()
        self._stop = False
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.run_dir = os.path.join(cfg.output_dir, cfg.run_id)
        self.highlights: List[dict] = []
        self.chronicle: List[dict] = []
        self.last_decision: Dict[str, dict] = {}
        self.report_paths: Dict[str, str] = {}
        self.client: Optional[OllamaClient] = None
        self.env = None
        self.personas = []
        self.memories: Dict[str, AgentMemory] = {}
        self.minds: Dict[str, AgentMind] = {}
        self.tracker = MetricsTracker()
        self.graph: Optional[SocialGraph] = None
        self.detectors: Optional[DetectorSuite] = None
        self.recorder: Optional[Recorder] = None
        self._unsubs: List[Callable[[], None]] = []
        self._chron_from = 1

    def pause(self) -> None:
        if self.status == "running":
            self._gate.clear()
            self.status = "paused"
            self._publish(Event("sim_paused", self.step, "Simulation paused", visibility="system"))

    def resume(self) -> None:
        if self.status == "paused":
            self._gate.set()
            self.status = "running"
            self._publish(Event("sim_resumed", self.step, "Simulation resumed", visibility="system"))

    def stop(self) -> None:
        self._stop = True
        self._gate.set()

    @property
    def active(self) -> bool:
        return self.status in ("created", "starting", "running", "paused", "finishing")

    def _publish(self, ev: Event) -> Event:
        return self.bus.publish(ev)

    def _flush_env(self) -> None:
        for ev in self.env.drain_events():
            self._publish(ev)

    def _persona_dicts(self) -> Dict[str, dict]:
        return {p.id: p.to_dict() for p in self.personas}

    async def _setup(self) -> None:
        cfg = self.cfg
        rng = random.Random(cfg.seed)
        self.personas = generate_personas(cfg.num_agents, cfg.environment, cfg.seed, cfg.archetype_mix or None,
                                          cfg.llm.temperature, cfg.llm.model_for if cfg.llm.backend == "ollama" else (lambda i: ""))
        self.env = build_environment(cfg, self.personas, rng)
        ids = [p.id for p in self.personas]
        self.graph = SocialGraph(ids)
        self.detectors = DetectorSuite(cfg.environment, ids)
        self.recorder = Recorder(self.run_dir)
        for fn in (self.recorder.on_event, self.tracker.on_event, self.graph.on_event, self.detectors.on_event):
            self._unsubs.append(self.bus.subscribe(fn))
        self.recorder.write_text("config.json", cfg.model_dump_json(indent=2))
        if cfg.llm.backend == "ollama":
            self.client = OllamaClient(cfg.llm.base_url, cfg.llm.timeout_s, cfg.llm.max_concurrency, cfg.llm.max_retries,
                                       cfg.llm.keep_alive, cfg.llm.num_ctx, cfg.llm.num_predict, cfg.llm.use_json_schema,
                                       transport=self.transport)
            health = await self.client.health()
            if not health["ok"]:
                raise RuntimeError(health["error"] + " — is `ollama serve` running? Or choose the heuristic backend.")
            needed = [p.model for p in self.personas] + ([cfg.llm.narrator_model] if cfg.llm.narrator_model else [])
            missing = await self.client.ensure_models(needed, cfg.llm.auto_pull)
            if missing:
                raise RuntimeError(f"Ollama is missing model(s) {missing}. Pull them with `ollama pull {missing[0]}` or enable auto-pull.")
        for idx, p in enumerate(self.personas):
            mem = AgentMemory(p)
            self.memories[p.id] = mem
            self.minds[p.id] = AgentMind(p, mem, cfg.llm.backend, cfg.llm, self.client, seed=cfg.seed * 31 + idx)
            if cfg.llm.backend == "ollama":
                self.recorder.write_text(f"prompts/system_{p.id}.txt", system_prompt(p, self.env))
        self.recorder.write_json("personas.json", self._persona_dicts())

    async def run(self) -> Dict[str, Any]:
        self.status = "starting"
        self.started_at = time.time()
        try:
            await self._setup()
            self.status = "running"
            self._publish(Event("sim_started", 0, f"{self.env.title}: {self.cfg.num_agents} agents, {self.cfg.steps} steps",
                                visibility="system", importance=2,
                                data={"config": self.cfg.model_dump(), "personas": self._persona_dicts(),
                                      "environment": {"key": self.env.key, "title": self.env.title, "blurb": self.env.blurb}}))
            for step in range(1, self.cfg.steps + 1):
                await self._gate.wait()
                if self._stop:
                    break
                await self._step(step)
                if self.cfg.step_delay_s:
                    await asyncio.sleep(self.cfg.step_delay_s)
            final = "stopped" if self._stop and self.step < self.cfg.steps else "completed"
            await self._finish(final)
        except asyncio.CancelledError:
            await self._finish("stopped")
            raise
        except Exception as e:
            self.error = f"{e.__class__.__name__}: {e}"
            log.error("simulation failed: %s\n%s", self.error, traceback.format_exc())
            self._publish(Event("sim_failed", self.step, self.error, visibility="system", importance=3,
                                data={"traceback": traceback.format_exc()[-4000:]}))
            await self._finish("failed")
        return self.summary()

    async def _step(self, step: int) -> None:
        env = self.env
        self.step = step
        env.begin_step(step)
        self._publish(Event("step_started", step, f"Step {step}", visibility="system", importance=0))
        self._flush_env()

        observations: Dict[str, str] = {}
        for aid in env.alive_ids():
            notices = env.pop_notices(aid)
            self.memories[aid].ingest(notices, step)
            observations[aid] = env.render_observation(aid, notices)
        if step == 1 and self.cfg.llm.backend == "ollama":
            first = env.alive_ids()[0]
            self.recorder.write_text(f"prompts/turn_{first}_step1.txt", turn_prompt(observations[first], self.memories[first], step))

        decisions: Dict[str, Decision] = {}

        async def think(aid: str) -> Decision:
            return await self.minds[aid].decide(env, observations[aid])

        tasks = [asyncio.create_task(think(aid)) for aid in observations]
        for fut in asyncio.as_completed(tasks):
            d = await fut
            decisions[d.agent] = d
            self.last_decision[d.agent] = {"step": step, "thought": d.thought, "mood": d.mood, "source": d.source,
                                           "actions": [a.summary() for a in d.actions], "latency": round(d.latency_s, 2)}
            if d.source == "error":
                self._publish(Event("llm_error", step, "; ".join(d.errors)[:300], actor=d.agent, visibility="system", importance=1))
            else:
                self._publish(Event("thought", step, d.thought, actor=d.agent, visibility="internal", importance=1,
                                    data={"mood": d.mood, "actions": [a.summary() for a in d.actions], "source": d.source,
                                          "latency_s": round(d.latency_s, 2), "tokens": d.tokens, "strategy": d.strategy_note}))

        order = list(decisions)
        random.Random(self.cfg.seed * 7_654_321 + step).shuffle(order)
        for aid in order:
            d = decisions[aid]
            feedback: List[str] = [f"(not executed) {e}" for e in d.errors] if d.source != "error" else \
                ["your mind was clouded this turn (the model failed); you did nothing"]
            for a in d.actions:
                try:
                    env.handle(aid, a)
                    feedback.append(f"ok: {a.summary()}")
                except ActionError as e:
                    feedback.append(f"FAILED: {a.summary()} — {e}")
                    self._publish(Event("action_rejected", step, f"{a.summary()} — {e}", actor=aid, visibility="internal",
                                        importance=0, data={"action": a.raw, "reason": str(e)}))
            self.memories[aid].record_feedback(feedback)
            self._flush_env()

        env.end_step(step)
        self._flush_env()
        env.ledger.check_invariants()

        row = self.tracker.close_step(step, env)
        worths = {a: round(env.net_worth(a) / 100, 2) for a in env.agents}
        self._publish(Event("metrics", step, "", visibility="system", importance=0, data={**row, "worths": worths}))
        for h in self.detectors.evaluate(step, env, row):
            self._add_highlight(step, h)
        if step % self.cfg.summary_every == 0:
            await self._chronicle(step)
        self.recorder.flush()
        self._publish(Event("step_completed", step, f"Step {step} complete", visibility="system", importance=0,
                            data={"network": self.network_snapshot(), "world": self.world_snapshot(), "llm": self.llm_stats()}))

    def _add_highlight(self, step: int, h: Highlight) -> None:
        d = {"step": step, "category": h.category, "title": h.title, "body": h.body, "severity": h.severity, "agents": h.agents, "data": h.data}
        self.highlights.append(d)
        self._publish(Event("highlight", step, h.title, actor=h.agents[0] if h.agents else None, targets=h.agents[1:],
                            visibility="system", importance=3, data=d))

    async def _chronicle(self, step: int) -> None:
        if step < self._chron_from:
            return
        window_h = [Highlight(h["category"], h["title"], h["body"], h["severity"], h["agents"]) for h in self.highlights
                    if self._chron_from <= h["step"] <= step]
        leaders = sorted(((a, self.env.net_worth(a) / 100) for a in self.env.agents), key=lambda kv: -kv[1])
        facts = facts_for_window(self._chron_from, step, self.tracker.rows, window_h, leaders)
        model = (self.cfg.llm.narrator_model or self.cfg.llm.model) if self.client else None
        text, source = await narrate(self._chron_from, step, facts, self.client, model)
        entry = {"start": self._chron_from, "end": step, "text": text, "source": source}
        self.chronicle.append(entry)
        self._publish(Event("summary", step, text, visibility="system", importance=2, data=entry))
        self._chron_from = step + 1

    async def _finish(self, status: str) -> None:
        self.status = "finishing"
        try:
            if self.env is not None and self.step >= self._chron_from:
                await self._chronicle(self.step)
            if self.env is not None and self.recorder is not None:
                builder = ReportBuilder(self.run_dir, self.cfg, self.env, self.tracker, self.graph, self.highlights, self.chronicle,
                                        self._persona_dicts(), {a: m.to_dict() for a, m in self.memories.items()}, self.llm_stats(), status)
                self.report_paths = await asyncio.to_thread(builder.write)
        except Exception as e:
            log.exception("report generation failed")
            self.error = (self.error or "") + f" | report failed: {e}"
        finally:
            self.status = status
            self.finished_at = time.time()
            self._publish(Event("sim_completed" if status != "failed" else "sim_failed", self.step,
                                f"Simulation {status} after {self.step} steps", visibility="system", importance=3,
                                data={"status": status, "run_id": self.cfg.run_id, "error": self.error,
                                      "report": bool(self.report_paths)}))
            if self.recorder:
                self.recorder.flush()
                self.recorder.close()
            for u in self._unsubs:
                u()
            self._unsubs = []
            if self.client:
                await self.client.aclose()

    def llm_stats(self) -> dict:
        return self.client.stats.to_dict() if self.client else {"backend": "heuristic"}

    def network_snapshot(self) -> dict:
        if not self.graph or not self.env:
            return {"nodes": [], "links": []}
        attrs = {p.id: {"archetype": p.archetype, "color": ARCHETYPES[p.archetype].color,
                        "worth": round(self.env.net_worth(p.id) / 100, 2), "syndicate": self.env.membership.get(p.id)}
                 for p in self.personas}
        return self.graph.snapshot(attrs)

    def world_snapshot(self) -> dict:
        env = self.env
        if env is None:
            return {}
        snap = env.public_snapshot()
        snap["metrics"] = env.env_metrics()
        if env.key == "auction_house":
            snap["lots"] = [{"id": l.id, "item": l.item, "format": l.format, "high_bid": l.high_bid / 100,
                             "high_bidder": l.high_bidder, "reserve": l.reserve / 100, "seller": "house" if l.seller == "__house__" else l.seller,
                             "bids": len(l.bids)} for l in env.lots.values() if l.status == "open"]
        if env.key == "commons":
            snap["proposals"] = [{"id": p.id, "rule": p.rule, "value": p.value, "yes": sum(1 for v in p.votes.values() if v),
                                  "no": sum(1 for v in p.votes.values() if not v)} for p in env.proposals.values() if p.status == "open"]
        return snap

    def agents_snapshot(self) -> List[dict]:
        env = self.env
        if env is None:
            return []
        out = []
        for p in self.personas:
            st = env.agents[p.id].stats
            ld = self.last_decision.get(p.id, {})
            out.append({
                "id": p.id, "archetype": p.archetype, "label": ARCHETYPES[p.archetype].label, "color": ARCHETYPES[p.archetype].color,
                "model": p.model, "cash": round(env.ledger.cash(p.id) / 100, 2), "worth": round(env.net_worth(p.id) / 100, 2),
                "start_worth": round(st.get("start_worth", 0) / 100, 2), "inventory": env.ledger.items(p.id),
                "mood": ld.get("mood") or self.memories[p.id].mood if p.id in self.memories else "",
                "thought": ld.get("thought", ""), "actions": ld.get("actions", []), "syndicate": env.membership.get(p.id),
                "reneges": st.get("reneges", 0), "scammed": st.get("scammed", 0), "trades": st.get("trades", 0),
                "objective": p.objective, "backstory": p.backstory, "traits": p.traits, "strategy": self.memories[p.id].strategy if p.id in self.memories else "",
                "relations": {k: round(v.trust, 2) for k, v in self.memories[p.id].relations.items()} if p.id in self.memories else {},
            })
        return out

    def summary(self) -> Dict[str, Any]:
        return {"run_id": self.cfg.run_id, "status": self.status, "step": self.step, "steps": self.cfg.steps,
                "error": self.error, "run_dir": self.run_dir, "report": self.report_paths,
                "highlights": len(self.highlights)}

    def snapshot(self, feed_limit: int = 400) -> Dict[str, Any]:
        feed = [e.to_dict() for e in list(self.bus.history)[-feed_limit * 3:]
                if e.kind not in ("metrics", "step_completed", "step_started", "production", "regrowth", "order_expired")][-feed_limit:]
        return {
            **self.summary(),
            "name": self.cfg.name,
            "environment": self.cfg.environment,
            "env_title": self.env.title if self.env else "",
            "config": self.cfg.model_dump(),
            "agents": self.agents_snapshot(),
            "metrics": [{k: v for k, v in r.items()} for r in self.tracker.rows],
            "highlights": self.highlights,
            "chronicle": self.chronicle,
            "feed": feed,
            "network": self.network_snapshot(),
            "world": self.world_snapshot(),
            "llm": self.llm_stats(),
        }
