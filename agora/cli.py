from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from typing import List, Optional

from agora import __author__, __version__
from agora.config import LLMConfig, SimulationConfig

def _cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn

    from agora.server.app import create_app

    app = create_app(a.output)
    print(f"Agora {__version__} by {__author__} — dashboard at http://{a.host}:{a.port}  (runs saved to ./{a.output})")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0

def _cmd_check(a: argparse.Namespace) -> int:
    from agora.llm.ollama import OllamaClient

    async def go() -> dict:
        async with OllamaClient(a.base_url, timeout_s=8) as c:
            return await c.health()

    h = asyncio.run(go())
    if h["ok"]:
        print(f"Ollama {h['version']} reachable at {h['base_url']}")
        print("Installed models:" + ("\n  " + "\n  ".join(h["models"]) if h["models"] else " none — try `ollama pull llama3.1:8b`"))
        return 0
    print(h["error"])
    return 1

def _cmd_run(a: argparse.Namespace) -> int:
    from agora.engine.runner import SimulationRunner

    pool = [m.strip() for m in (a.model_pool or "").split(",") if m.strip()]
    base: dict = {}
    if a.config:
        with open(a.config, encoding="utf-8") as f:
            base = json.load(f)
        base.pop("run_id", None)
    for key, val in (("name", a.name), ("environment", a.env), ("num_agents", a.agents), ("steps", a.steps),
                     ("seed", a.seed), ("summary_every", a.summary_every)):
        if val is not None:
            base[key] = val
    base["output_dir"] = a.output
    llm = dict(base.get("llm") or {})
    for key, val in (("backend", a.backend), ("base_url", a.base_url), ("model", a.model), ("max_concurrency", a.concurrency)):
        if val is not None:
            llm[key] = val
    if pool:
        llm["model_pool"] = pool
    if a.pull:
        llm["auto_pull"] = True
    base["llm"] = LLMConfig(**llm)
    cfg = SimulationConfig(**base)
    runner = SimulationRunner(cfg)

    def show(ev) -> None:
        if ev.kind == "highlight":
            print(f"  ★ [step {ev.step:>3}] {ev.text}")
        elif ev.kind == "summary":
            print(f"\n  ✎ {ev.text}\n")
        elif ev.kind == "step_completed" and not a.quiet:
            m = runner.tracker.last() or {}
            print(f"step {ev.step:>3}/{cfg.steps}  gini {m.get('gini', 0):.3f}  trades {m.get('trades', 0):>3}  "
                  f"msgs {m.get('public_msgs', 0) + m.get('private_msgs', 0):>3}  rejected {m.get('rejected_actions', 0):>3}  llm errors {m.get('llm_errors', 0)}")
        elif ev.kind in ("sim_failed",):
            print(f"FAILED: {ev.text}")

    runner.bus.subscribe(show)
    print(f"Agora {__version__}: {cfg.environment}, {cfg.num_agents} agents, {cfg.steps} steps, minds = "
          f"{cfg.llm.model if cfg.llm.backend == 'ollama' else 'heuristic baseline'}  (run {cfg.run_id})")
    s = asyncio.run(runner.run())
    print(f"\nStatus: {s['status']}.  Report: {s['report'].get('report_html', '(none)')}")
    if s.get("error"):
        print(f"Error: {s['error']}")
    return 0 if s["status"] in ("completed", "stopped") else 2

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agora", description="Autonomous multi-agent mechanism design lab")
    p.add_argument("--version", action="version", version=f"agora {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="start the dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--output", default="runs")
    s.set_defaults(fn=_cmd_serve)

    c = sub.add_parser("check", help="check the Ollama server")
    c.add_argument("--base-url", default="http://localhost:11434")
    c.set_defaults(fn=_cmd_check)

    r = sub.add_parser("run", help="run a simulation headless")
    r.add_argument("--env", choices=["marketplace", "auction_house", "commons"])
    r.add_argument("--agents", type=int, help="number of agents (2-20, default 12)")
    r.add_argument("--steps", type=int, help="number of steps (default 40)")
    r.add_argument("--seed", type=int)
    r.add_argument("--name")
    r.add_argument("--backend", choices=["ollama", "heuristic"], help="default: ollama")
    r.add_argument("--model", help="Ollama model tag (default llama3.1:8b)")
    r.add_argument("--model-pool", default="", help="comma-separated models assigned round-robin to agents")
    r.add_argument("--base-url")
    r.add_argument("--concurrency", type=int, help="max parallel generations (match OLLAMA_NUM_PARALLEL)")
    r.add_argument("--pull", action="store_true", help="pull missing models automatically")
    r.add_argument("--summary-every", type=int)
    r.add_argument("--config", help="JSON file with a full SimulationConfig (CLI flags override name/env/agents/steps/seed)")
    r.add_argument("--output", default="runs")
    r.add_argument("--quiet", action="store_true")
    r.set_defaults(fn=_cmd_run)
    return p
def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    return args.fn(args)

if __name__ == "__main__":
    sys.exit(main())
