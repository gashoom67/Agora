# Agora — an autonomous multi-agent mechanism-design lab

*Created by **Gashoom**.*

Agora instantiates a small economy, populates it with up to **20 LLM agents** running locally on
[Ollama](https://ollama.com), each with its own psychology and private agenda, and then leaves it
alone. Nobody scripts the plot: alliances, cartels, scams, bidding rings, market corners and
tragedies of the commons emerge — or don't — from incentives, institutions and character. The
platform watches everything, flags what is interesting, and writes it up.

```
┌─────────────────────────── Dashboard (FastAPI + WebSocket, vanilla JS) ───────────────────────────┐
│ configure world & population · live constellation · thought/deal feed · charts · chronicle         │
└───────────────────────────────────────────────▲───────────────────────────────────────────────────┘
                                                │ events
┌───────────────────── Engine (async loop) ─────┴───────────────────────────────────────────────────┐
│ begin_step → perceive → deliberate (concurrent) → resolve (random serial order) → end_step → audit │
└──────▲────────────────────────────────────▲──────────────────────────────────────▲────────────────┘
       │                                    │                                      │
┌──────┴───────────┐   ┌────────────────────┴───────────────────┐   ┌──────────────┴─────────────────┐
│ Environment      │   │ Agent cognition                        │   │ Observation & logging          │
│ engine           │   │ persona (Beta-sampled traits)          │   │ event bus → JSONL recorder     │
│ ledger (exact,   │   │ memory (episodic, trust, grievances)   │   │ metrics (Gini, Theil, HHI …)   │
│  double-entry)   │   │ prompts (identity + physics)           │   │ social graph (networkx)        │
│ contracts/escrow │   │ Ollama client (schema, retries, pool)  │   │ emergent-event detectors       │
│ marketplace /    │   │ heuristic baseline (control group)     │   │ chronicle (LLM narrator)       │
│ auctions/commons │   │                                        │   │ report (charts, HTML, zip)     │
└──────────────────┘   └────────────────────────────────────────┘   └────────────────────────────────┘
```

## Quick start

```bash
# 1. Python 3.10+
pip install -e .            # or: pip install .

# 2. A local model (any instruction-tuned model works; 7–9B is the sweet spot)
ollama pull llama3.1:8b
# For 20 agents, let Ollama serve several requests at once:
OLLAMA_NUM_PARALLEL=4 ollama serve

# 3. Dashboard
agora serve                 # → http://127.0.0.1:8765
```

Pick a world, a population mix and the rules, press **Start simulation**, and watch.
No Ollama yet? Choose **Heuristic baseline** in the *Minds* panel — the whole platform runs with
rule-based agents driven by the same personality traits.

Headless runs:

```bash
agora check                                            # is Ollama up, which models are installed?
agora run --env commons --agents 20 --steps 60 --model llama3.1:8b
agora run --env auction_house --model-pool "llama3.1:8b,qwen2.5:7b,mistral:7b"   # mixed minds
agora run --config examples/commons_governance.json   # full config from a file
agora run --env marketplace --backend heuristic --steps 100                        # no LLM
```

### Hardware & timing

Each step makes one LLM call per agent (plus one narrator call every few steps). Prompts are
≈1.5–2.5k tokens, replies ≈150–300 tokens. With an 8B model on a single consumer GPU and
`OLLAMA_NUM_PARALLEL=4`, a 20-agent step typically takes 20–60 s. Set *Parallel generations* in the
dashboard (or `--concurrency`) to match `OLLAMA_NUM_PARALLEL`. Smaller models (3B) are faster but
follow the action format less reliably; invalid actions are rejected with a reason the agent sees
on its next turn, so they learn the rules as they go.

## The three worlds

| World | Mechanism | What tends to emerge |
|---|---|---|
| **Open Marketplace** | Specialist producers, crafting recipes, mandatory food, *induced private values*; a price–time-priority limit order book per good plus bilateral barter/contracts. | Price discovery, specialisation, hoarding and grain corners, cartel pricing inside syndicates, trust scams. |
| **Auction House** | Rare lots with hidden common values; noisy private appraisals; first-price sealed, Vickrey and English auctions (or rotating); reserve prices; re-consignment; shill-bid detection & fines. | Winner's curse, bid shading, bidding rings, shill bidding, lemons sold on trust. |
| **Resource Commons** | Logistic fishery with depensation, Cournot price externality, stock-dependent cost, upkeep; agents can legislate quotas, landing taxes (rebated) and monitoring by vote. | Tragedy of the commons, Ostrom-style self-governance, free-riding on enforcement, collapse or recovery. |

Shared institutions in every world: public square, private messages, **escrow** contracts (atomic,
fee) vs **trust** contracts (no fee — the counterparty pays first and the proposer may *renege*),
a public default registry (toggleable), public accusations and **syndicates** with private channels.
Every rule is a parameter in the *Rules of the world* panel, so you can run mechanism-design
experiments: does a public default registry reduce scams? Does Vickrey beat first-price on
revenue with these minds? Does monitoring at 25 % make quotas self-enforcing?

## Agents

Eight archetypes act as **priors**, not scripts: honest merchant, risk-averse hoarder,
opportunistic speculator, erratic gambler, trickster, coalition builder, steward and enforcer.
Each agent's nine traits (honesty, risk tolerance, sociability, agreeableness, conscientiousness,
patience, impulsivity, vindictiveness, greed) are sampled from Beta distributions centred on its
archetype, then rendered into natural language and into numbers — sampling temperature, CRRA risk
aversion and discount factor. Each agent also gets a backstory, a speaking voice, a quirk and a
private objective it never has to reveal.

Memory is explicit and inspectable: a short-term episodic buffer, the results of its own last
actions, a long-term plan the agent rewrites each turn, an asymmetric trust model of every other
agent (betrayal hurts more than kindness helps, scaled by vindictiveness) and a list of unforgiven
grievances. Click any agent in the dashboard to see all of it.

## What you get from a run

Everything lands in `runs/<run_id>/`:

| File | Contents |
|---|---|
| `report.html` | Self-contained case-study page: headline numbers, highlights, chronicle, charts, leaderboard, config |
| `report.md` | The same in Markdown (charts in `charts/`) |
| `events.jsonl` | Every event: thoughts, messages, orders, trades, contracts, bids, votes, rejections |
| `metrics.csv` | Per-step Gini, Theil, top-20 % share, trade value, messages, reneges, prices / stock / auction stats |
| `agents.csv`, `agents_final.json` | Final outcomes, traits, memories and relationship maps per agent |
| `network.graphml` | The social interaction network (open in Gephi / networkx) |
| `highlights.json`, `chronicle.md` | Flagged emergent events and the narrator's dispatches |
| `prompts/` | The exact system prompt of every agent and a sample turn prompt |
| `agora-<run_id>.zip` | All of the above, one download |

The maths behind every metric, mechanism and detector is written up in the companion document
`agora_maths.tex` (kept separate from the code).

## Development

```bash
pip install -e ".[dev]"
pytest                      # 45 tests: ledger conservation, order book, auctions, commons,
                            # contracts, metrics, Ollama client, full 20-agent runs, server + WebSocket
python -m agora.testing.fake_ollama --port 11500   # protocol-faithful fake Ollama for demos/CI
```

The fake server speaks the real Ollama HTTP API and injects realistic failure modes (prose around
JSON, `<think>` tags, malformed JSON, HTTP 500s), so the full LLM pipeline is tested without a GPU.

### Project layout

```
agora/
  config.py                 typed, validated SimulationConfig (pydantic)
  core/                     money (fixed-point), ledger (double-entry), events, action schema
  environments/             base institutions + marketplace, auction house, commons
  agents/                   personas, memory, prompts, LLM/heuristic minds
  llm/ollama.py             async client: semaphore, JSON-schema outputs, retries, telemetry
  observe/                  bus, recorder, metrics, network, detectors, chronicle, report
  engine/runner.py          the autonomous loop
  server/                   FastAPI app + static dashboard (vanilla JS, vendored Chart.js)
  testing/fake_ollama.py    fake Ollama server
tests/
examples/                   ready-made experiment configs
```

### Robustness notes

* The ledger is exact (integer cents) and audited every step: total cash and every good are
  conserved, nothing is ever negative. Any violation aborts the run with a traceback.
* Model output is never trusted: tolerant JSON extraction → schema normalisation (aliases, string
  numbers) → rule validation in the environment. Rejections are fed back to the agent.
* Messages between agents are quoted as data in prompts and the dashboard inserts all agent text
  with `textContent`, never as HTML.
* A slow dashboard client can never stall the simulation (bounded per-client queues).
* Runs are reproducible for a given seed with the heuristic backend; with an LLM backend the seed
  is also passed to Ollama, but GPU nondeterminism may still vary outputs.

## Credits

Designed and built by **Ghashem Sari Ghashem Almansoori** — architecture, environment mechanisms, agent
psychology and memory model, analytics and the companion mathematical note (`agora_maths.tex`).

## License

MIT © 2026 Ghashem Sari Ghashem Almansoori. Chart.js (MIT) is vendored in `agora/server/static/vendor/`.
