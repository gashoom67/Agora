
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, Optional, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from agora import __version__
from agora.agents.persona import ARCHETYPES, DEFAULT_MIX
from agora.config import MAX_AGENTS, SimulationConfig
from agora.core.events import Event
from agora.engine.runner import SimulationRunner
from agora.environments.registry import describe_environments
from agora.llm.ollama import OllamaClient
from agora.observe.bus import EventBus

log = logging.getLogger("agora.server")
STATIC = os.path.join(os.path.dirname(__file__), "static")
RUN_ID = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
DOWNLOADABLE = {"report.html", "report.md", "metrics.csv", "agents.csv", "events.jsonl", "highlights.json",
                "chronicle.md", "network.graphml", "summary.json", "config.json", "personas.json", "agents_final.json"}

class Broadcaster:
    

    def __init__(self) -> None:
        self.clients: Set[asyncio.Queue] = set()

    def add(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=3000)
        self.clients.add(q)
        return q

    def remove(self, q: asyncio.Queue) -> None:
        self.clients.discard(q)

    def send(self, msg: Dict[str, Any]) -> None:
        data = json.dumps(msg, default=str, ensure_ascii=False)
        for q in list(self.clients):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(data)

    def on_event(self, ev: Event) -> None:
        self.send({"type": "event", "event": ev.to_dict()})

class Session:
    def __init__(self, output_dir: str) -> None:
        self.output_dir = output_dir
        self.runner: Optional[SimulationRunner] = None
        self.task: Optional[asyncio.Task] = None
        self.broadcaster = Broadcaster()

    @property
    def active(self) -> bool:
        return self.runner is not None and self.task is not None and not self.task.done()

    async def start(self, cfg: SimulationConfig) -> SimulationRunner:
        if self.active:
            raise HTTPException(409, "a simulation is already running — stop it first")
        cfg.output_dir = self.output_dir
        bus = EventBus()
        bus.subscribe(self.broadcaster.on_event)
        self.runner = SimulationRunner(cfg, bus)
        self.broadcaster.send({"type": "reset", "run_id": cfg.run_id})
        self.task = asyncio.create_task(self.runner.run())
        return self.runner

    async def shutdown(self) -> None:
        if self.active and self.runner:
            self.runner.stop()
            try:
                await asyncio.wait_for(self.task, timeout=30)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.task.cancel()

def create_app(output_dir: str = "runs") -> FastAPI:
    os.makedirs(output_dir, exist_ok=True)
    app = FastAPI(title="Agora", version=__version__)
    session = Session(output_dir)
    app.state.session = session

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await session.shutdown()

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> FileResponse:
        return FileResponse(os.path.join(STATIC, "index.html"))

    @app.get("/api/meta")
    async def meta() -> Dict[str, Any]:
        defaults = SimulationConfig().model_dump()
        defaults.pop("run_id", None)
        return {
            "version": __version__,
            "max_agents": MAX_AGENTS,
            "environments": describe_environments(),
            "archetypes": [{"key": k, "label": a.label, "color": a.color, "essence": a.essence, "default_weight": DEFAULT_MIX[k]}
                           for k, a in ARCHETYPES.items()],
            "defaults": defaults,
        }

    @app.get("/api/ollama")
    async def ollama_status(base_url: str = "http://localhost:11434") -> Dict[str, Any]:
        if not re.match(r"^https?://[\w.\-:\[\]]+(/.*)?$", base_url):
            raise HTTPException(400, "base_url must be an http(s) URL")
        async with OllamaClient(base_url, timeout_s=8) as c:
            return await c.health()

    @app.post("/api/sim/start")
    async def start(body: Dict[str, Any]) -> Dict[str, Any]:
        body = {k: v for k, v in body.items() if k not in ("run_id", "output_dir")}
        try:
            cfg = SimulationConfig(**body)
        except ValidationError as e:
            return JSONResponse(status_code=422, content={"detail": json.loads(e.json())})
        runner = await session.start(cfg)
        return {"run_id": runner.cfg.run_id, "status": runner.status}

    def _runner() -> SimulationRunner:
        if session.runner is None:
            raise HTTPException(404, "no simulation has been started")
        return session.runner

    @app.post("/api/sim/pause")
    async def pause() -> Dict[str, Any]:
        r = _runner()
        r.pause()
        return r.summary()

    @app.post("/api/sim/resume")
    async def resume() -> Dict[str, Any]:
        r = _runner()
        r.resume()
        return r.summary()

    @app.post("/api/sim/stop")
    async def stop() -> Dict[str, Any]:
        r = _runner()
        r.stop()
        return r.summary()

    @app.get("/api/sim/state")
    async def state() -> Dict[str, Any]:
        if session.runner is None:
            return {"status": "idle"}
        return session.runner.snapshot()

    @app.get("/api/runs")
    async def runs() -> Dict[str, Any]:
        out = []
        for d in sorted(os.listdir(output_dir), reverse=True):
            p = os.path.join(output_dir, d, "summary.json")
            if RUN_ID.match(d) and os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as f:
                        s = json.load(f)
                    out.append({k: s.get(k) for k in ("run_id", "name", "environment", "status", "steps_completed", "agents",
                                                       "gini_end", "reneges", "highlights", "model")})
                except (OSError, json.JSONDecodeError):
                    continue
        return {"runs": out[:100]}

    def _run_path(run_id: str, name: str) -> str:
        if not RUN_ID.match(run_id):
            raise HTTPException(400, "bad run id")
        path = os.path.join(output_dir, run_id, name)
        if not os.path.isfile(path):
            raise HTTPException(404, f"{name} not found for run {run_id}")
        return path

    @app.get("/api/runs/{run_id}/report", response_class=HTMLResponse)
    async def report(run_id: str) -> FileResponse:
        return FileResponse(_run_path(run_id, "report.html"), media_type="text/html")

    @app.get("/api/runs/{run_id}/bundle")
    async def bundle(run_id: str) -> FileResponse:
        name = f"agora-{run_id}.zip"
        return FileResponse(_run_path(run_id, name), media_type="application/zip", filename=name)

    @app.get("/api/runs/{run_id}/files/{name}")
    async def run_file(run_id: str, name: str) -> FileResponse:
        if name not in DOWNLOADABLE:
            raise HTTPException(404, "file not available")
        return FileResponse(_run_path(run_id, name), filename=name)

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        q = session.broadcaster.add()
        try:
            snap = session.runner.snapshot() if session.runner else {"status": "idle"}
            await socket.send_text(json.dumps({"type": "snapshot", "state": snap}, default=str, ensure_ascii=False))

            async def pump() -> None:
                while True:
                    await socket.send_text(await q.get())

            async def drain() -> None:
                while True:
                    await socket.receive_text()

            done, pending = await asyncio.wait([asyncio.create_task(pump()), asyncio.create_task(drain())],
                                               return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                if t.exception() and not isinstance(t.exception(), WebSocketDisconnect):
                    log.debug("websocket closed: %s", t.exception())
        except WebSocketDisconnect:
            pass
        finally:
            session.broadcaster.remove(q)

    return app
