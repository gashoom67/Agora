from __future__ import annotations

import base64
import csv
import html
import json
import os
import zipfile
from collections import defaultdict
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
import networkx as nx
import numpy as np

from agora import __author__, __version__
from agora.agents.persona import ARCHETYPES
from agora.observe.metrics import gini, lorenz

SURFACE = "#fcfcfb"
INK = "#1b2a29"
INK2 = "#52514e"
GRID = "#e4e6e1"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
NEG = "#d03b3b"

def _style(ax, title: str, xlabel: str = "step", ylabel: str = "") -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10, fontweight="bold")
    ax.set_xlabel(xlabel, color=INK2, fontsize=9)
    ax.set_ylabel(ylabel, color=INK2, fontsize=9)
    ax.tick_params(colors=INK2, labelsize=8, length=0)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)

def _fig(w: float = 7.2, h: float = 3.4, ncols: int = 1):
    fig, axes = plt.subplots(1, ncols, figsize=(w, h), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    return fig, axes

def _save(fig, path: str) -> None:
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)

def _label_end(ax, x, y, text, color=INK):
    ax.annotate(text, (x, y), xytext=(4, 0), textcoords="offset points", fontsize=8, color=color, va="center")

class ReportBuilder:
    def __init__(self, run_dir: str, cfg, env, tracker, graph, highlights: List[dict], chronicle: List[dict],
                 personas: Dict[str, dict], memories: Dict[str, dict], llm_stats: dict, status: str) -> None:
        self.dir = run_dir
        self.cfg = cfg
        self.env = env
        self.tracker = tracker
        self.graph = graph
        self.highlights = highlights
        self.chronicle = chronicle
        self.personas = personas
        self.memories = memories
        self.llm_stats = llm_stats
        self.status = status
        self.charts_dir = os.path.join(run_dir, "charts")
        os.makedirs(self.charts_dir, exist_ok=True)
        self.charts: List[tuple] = []

    def agent_table(self) -> List[dict]:
        rows = []
        env = self.env
        cent = self.graph.centrality()
        for aid, st in env.agents.items():
            p = self.personas[aid]
            start = st.stats.get("start_worth", 0) / 100
            end = env.net_worth(aid) / 100
            rows.append({
                "agent": aid, "archetype": p["archetype"], "archetype_label": p["archetype_label"],
                "model": p.get("model", ""), "start_worth": round(start, 2), "final_worth": round(end, 2),
                "return_pct": round((end / start - 1) * 100, 1) if start else 0.0,
                "final_score": round(env.final_score(aid) / 100, 2),
                "trades": st.stats.get("trades", 0), "contracts_settled": st.stats.get("contracts_settled", 0),
                "reneges": st.stats.get("reneges", 0), "times_scammed": st.stats.get("scammed", 0),
                "defaults": st.stats.get("defaults", 0), "accusations_received": env.accusations.get(aid, 0),
                "syndicate": env.membership.get(aid, ""),
                "degree": cent.get(aid, {}).get("degree", 0), "betweenness": cent.get(aid, {}).get("betweenness", 0),
                "honesty": p["traits"]["honesty"], "risk_tolerance": p["traits"]["risk_tolerance"], "greed": p["traits"]["greed"],
            })
        rows.sort(key=lambda r: -r["final_worth"])
        return rows

    def archetype_outcomes(self, table: List[dict]) -> List[dict]:
        agg = defaultdict(list)
        for r in table:
            agg[r["archetype"]].append(r)
        out = []
        for k, rs in agg.items():
            out.append({"archetype": k, "label": ARCHETYPES[k].label, "n": len(rs),
                        "mean_return_pct": round(float(np.mean([r["return_pct"] for r in rs])), 1),
                        "reneges": sum(r["reneges"] for r in rs), "scammed": sum(r["times_scammed"] for r in rs)})
        out.sort(key=lambda r: -r["mean_return_pct"])
        return out

    def _chart(self, name: str, title: str, fig) -> None:
        path = os.path.join(self.charts_dir, name)
        _save(fig, path)
        self.charts.append((name, title))

    def build_charts(self, table: List[dict], outcomes: List[dict]) -> None:
        rows = self.tracker.rows
        if not rows:
            return
        steps = [r["step"] for r in rows]

        fig, ax = _fig()
        g = [r["gini"] for r in rows]
        t = [r["top20_share"] for r in rows]
        ax.plot(steps, g, color=SERIES[0], lw=2, label="Gini coefficient")
        ax.plot(steps, t, color=SERIES[1], lw=2, label="Top-20% wealth share")
        _label_end(ax, steps[-1], g[-1], f"{g[-1]:.2f}")
        _label_end(ax, steps[-1], t[-1], f"{t[-1]:.2f}")
        ax.set_ylim(0, 1)
        _style(ax, "Wealth inequality over time", ylabel="index (0–1)")
        ax.legend(frameon=False, fontsize=8, loc="upper left")
        self._chart("inequality.png", "Wealth inequality over time", fig)

        fig, ax = _fig(4.6, 4.2)
        first = [w[0] for w in self.tracker.worth_history.values() if w]
        last = [w[-1] for w in self.tracker.worth_history.values() if w]
        x0, y0 = lorenz(first)
        x1, y1 = lorenz(last)
        ax.plot([0, 1], [0, 1], color=INK2, lw=1, ls="--", label="Perfect equality")
        ax.plot(x0, y0, color=SERIES[0], lw=2, label=f"Step {steps[0]} (G={gini(first):.2f})")
        ax.plot(x1, y1, color=SERIES[1], lw=2, label=f"Step {steps[-1]} (G={gini(last):.2f})")
        _style(ax, "Lorenz curves", xlabel="cumulative share of agents", ylabel="cumulative share of wealth")
        ax.legend(frameon=False, fontsize=8, loc="upper left")
        self._chart("lorenz.png", "Lorenz curves, start vs end", fig)

        fig, ax = _fig(7.2, 4.0)
        seen = set()
        for aid, ws in self.tracker.worth_history.items():
            arch = self.personas[aid]["archetype"]
            col = ARCHETYPES[arch].color
            lab = ARCHETYPES[arch].label if arch not in seen else None
            seen.add(arch)
            ax.plot(steps[:len(ws)], ws, color=col, lw=1.6, alpha=0.9, label=lab)
        top = sorted(self.tracker.worth_history.items(), key=lambda kv: -kv[1][-1])[:3]
        for aid, ws in top:
            _label_end(ax, steps[len(ws) - 1], ws[-1], aid)
        _style(ax, "Net worth per agent (coloured by archetype)", ylabel="coins (mark-to-market)")
        ax.legend(frameon=False, fontsize=7, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.16))
        self._chart("wealth_trajectories.png", "Net worth trajectories", fig)

        fig, axes = _fig(7.6, 3.2, ncols=2)
        ax1, ax2 = axes
        tv = [r["trade_value"] for r in rows]
        ax1.bar(steps, tv, color=SERIES[0], width=0.8)
        _style(ax1, "Value exchanged per step", ylabel="coins")
        pub = [r["public_msgs"] for r in rows]
        prv = [r["private_msgs"] for r in rows]
        ax2.plot(steps, pub, color=SERIES[0], lw=2, label="public")
        ax2.plot(steps, prv, color=SERIES[1], lw=2, label="private")
        _style(ax2, "Messages per step", ylabel="messages")
        ax2.legend(frameon=False, fontsize=8)
        self._chart("activity.png", "Trade value and communication", fig)

        envk = self.env.key
        if envk == "marketplace":
            fig, ax = _fig(7.2, 3.8)
            goods = list(rows[-1]["env"]["prices"])
            for i, gname in enumerate(goods):
                ys = [r["env"]["prices"][gname] for r in rows]
                ax.plot(steps, ys, color=SERIES[i % len(SERIES)], lw=2, label=gname)
                _label_end(ax, steps[-1], ys[-1], gname)
            ax.set_yscale("log")
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
            ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
            _style(ax, "Reference prices (EWMA of trades, log scale)", ylabel="coins per unit")
            ax.legend(frameon=False, fontsize=7, ncol=7, loc="upper center", bbox_to_anchor=(0.5, -0.18))
            self._chart("prices.png", "Price formation", fig)
        elif envk == "commons":
            fig, axes = _fig(7.6, 3.2, ncols=2)
            ax1, ax2 = axes
            sf = [r["env"]["stock_fraction"] for r in rows]
            ax1.plot(steps, sf, color=SERIES[2], lw=2)
            ax1.axhline(0.5, color=INK2, lw=1, ls="--")
            ax1.annotate("MSY stock (K/2)", (steps[0], 0.5), xytext=(2, 4), textcoords="offset points", fontsize=7, color=INK2)
            ax1.axhline(self.cfg.commons.collapse_fraction, color=NEG, lw=1, ls=":")
            ax1.set_ylim(0, 1.05)
            _style(ax1, "Fish stock (fraction of K)", ylabel="S / K")
            hv = [r["env"]["harvest"] for r in rows]
            gr = [r["env"]["growth"] for r in rows]
            ax2.bar(steps, hv, color=SERIES[0], width=0.8, label="total catch")
            ax2.plot(steps, gr, color=SERIES[1], lw=2, label="regrowth")
            _style(ax2, "Catch vs regrowth", ylabel="fish")
            ax2.legend(frameon=False, fontsize=8)
            self._chart("commons.png", "Resource dynamics", fig)
        else:
            lots = [l for l in self.env.closed if l.status == "sold"]
            if lots:
                fig, ax = _fig(7.2, 3.6)
                for i, f in enumerate(("first_price", "vickrey", "english")):
                    pts = [(l.true_value / 100, l.price / 100) for l in lots if l.format == f]
                    if pts:
                        xs, ys = zip(*pts)
                        ax.scatter(xs, ys, s=36, color=SERIES[i], edgecolor=SURFACE, linewidth=1.5, label=f.replace("_", "-"))
                lim = max(max(l.true_value, l.price) for l in lots) / 100 * 1.08
                ax.plot([0, lim], [0, lim], color=INK2, lw=1, ls="--")
                ax.annotate("price = true value", (lim * 0.62, lim * 0.62), xytext=(4, -10), textcoords="offset points", fontsize=7, color=INK2)
                _style(ax, "Auction prices vs true values (above the line = winner's curse)", xlabel="true value", ylabel="price paid")
                ax.legend(frameon=False, fontsize=8, loc="upper left")
                self._chart("auctions.png", "Price discovery by auction format", fig)

        fig, ax = _fig(7.2, 0.45 * len(outcomes) + 1.2)
        labels = [o["label"] for o in outcomes][::-1]
        vals = [o["mean_return_pct"] for o in outcomes][::-1]
        cols = [ARCHETYPES[o["archetype"]].color for o in outcomes][::-1]
        ax.barh(labels, vals, color=cols, height=0.6)
        for i, v in enumerate(vals):
            ax.annotate(f"{v:+.1f}%", (v, i), xytext=(4 if v >= 0 else -4, 0), textcoords="offset points",
                        ha="left" if v >= 0 else "right", va="center", fontsize=8, color=INK)
        ax.axvline(0, color=INK2, lw=1)
        _style(ax, "Mean return by archetype", xlabel="% change in net worth", ylabel="")
        self._chart("archetypes.png", "Outcomes by archetype", fig)

        fig, ax = _fig(6.8, 6.8)
        u = self.graph.undirected()
        if u.number_of_nodes():
            pos = nx.spring_layout(u, weight="weight", seed=self.cfg.seed, k=1.4 / max(1, np.sqrt(u.number_of_nodes())))
            worths = {r["agent"]: r["final_worth"] for r in table}
            mx = max(max(worths.values()), 1)
            ws = [d["weight"] for _, _, d in u.edges(data=True)]
            mw = max(ws) if ws else 1
            floor = float(np.percentile(ws, 50)) if len(ws) > 30 else 0.0
            for a, b, d in u.edges(data=True):
                val = d["valence_sum"] / max(d["weight"], 1e-6)
                if d["weight"] < floor and val >= 0:
                    continue
                ax.plot([pos[a][0], pos[b][0]], [pos[a][1], pos[b][1]], color=NEG if val < 0 else "#9aa39c",
                        lw=0.5 + 3.5 * d["weight"] / mw, alpha=0.75, zorder=1)
            for n in u.nodes:
                arch = self.personas[n]["archetype"]
                ax.scatter(*pos[n], s=80 + 600 * max(worths.get(n, 0), 0) / mx, color=ARCHETYPES[arch].color,
                           edgecolor=SURFACE, linewidth=2, zorder=2)
                ax.annotate(n, pos[n], xytext=(0, 9), textcoords="offset points", ha="center", fontsize=8, color=INK, zorder=3)
            handles = [plt.Line2D([], [], marker="o", ls="", color=ARCHETYPES[k].color, label=ARCHETYPES[k].label)
                       for k in sorted({self.personas[n]["archetype"] for n in u.nodes})]
            handles.append(plt.Line2D([], [], color=NEG, lw=2, label="hostile tie"))
            ax.legend(handles=handles, frameon=False, fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=3)
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)
        ax.set_facecolor(SURFACE)
        ax.set_title("Social network: strongest ties (node size = final wealth)", loc="left", fontsize=11, color=INK, fontweight="bold")
        self._chart("network.png", "Social network map", fig)

    def write(self) -> Dict[str, str]:
        table = self.agent_table()
        outcomes = self.archetype_outcomes(table)
        self.build_charts(table, outcomes)
        flat = self.tracker.flat_rows()
        if flat:
            keys: List[str] = []
            for r in flat:
                for k in r:
                    if k not in keys:
                        keys.append(k)
            with open(os.path.join(self.dir, "metrics.csv"), "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerows(flat)
        if table:
            with open(os.path.join(self.dir, "agents.csv"), "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(table[0]))
                w.writeheader()
                w.writerows(table)
        with open(os.path.join(self.dir, "highlights.json"), "w", encoding="utf-8") as f:
            json.dump(self.highlights, f, indent=2, ensure_ascii=False)
        with open(os.path.join(self.dir, "chronicle.md"), "w", encoding="utf-8") as f:
            f.write("# Chronicle\n\n" + "\n\n".join(f"**Steps {c['start']}–{c['end']}.** {c['text']}" for c in self.chronicle) + "\n")
        with open(os.path.join(self.dir, "agents_final.json"), "w", encoding="utf-8") as f:
            json.dump({aid: {"persona": self.personas[aid], "memory": self.memories.get(aid, {}),
                             "stats": dict(self.env.agents[aid].stats)} for aid in self.env.agents}, f, indent=2, default=str)
        self.graph.export_graphml(os.path.join(self.dir, "network.graphml"))
        net = {"density": round(self.graph.density(), 4), "modularity": round(self.graph.modularity(), 4),
               "communities": self.graph.communities()}
        summary = self._summary(table, outcomes, net)
        with open(os.path.join(self.dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)
        md = self._markdown(summary, table, outcomes, net)
        with open(os.path.join(self.dir, "report.md"), "w", encoding="utf-8") as f:
            f.write(md)
        with open(os.path.join(self.dir, "report.html"), "w", encoding="utf-8") as f:
            f.write(self._html(summary, table, outcomes, net))
        zpath = self.bundle()
        return {"report_html": os.path.join(self.dir, "report.html"), "bundle": zpath}

    def _summary(self, table, outcomes, net) -> dict:
        rows = self.tracker.rows
        cum = self.tracker.cumulative
        return {
            "run_id": self.cfg.run_id, "name": self.cfg.name, "environment": self.env.title, "status": self.status,
            "steps_completed": rows[-1]["step"] if rows else 0, "agents": len(table), "backend": self.cfg.llm.backend,
            "model": self.cfg.llm.model if self.cfg.llm.backend == "ollama" else "heuristic baseline",
            "gini_start": round(rows[0]["gini"], 3) if rows else None, "gini_end": round(rows[-1]["gini"], 3) if rows else None,
            "gini_peak": round(max(r["gini"] for r in rows), 3) if rows else None,
            "total_trades": cum["trade"], "contracts_settled": cum["contract_settled"], "reneges": cum["contract_reneged"],
            "defaults": cum["contract_defaulted"], "accusations": cum["accusation"], "public_msgs": cum["say"],
            "private_msgs": cum["dm"] + cum["syndicate_msg"], "rejected_actions": cum["action_rejected"],
            "llm_errors": cum["llm_error"], "highlights": len(self.highlights), "network": net,
            "winner": table[0]["agent"] if table else None, "best_archetype": outcomes[0]["label"] if outcomes else None,
            "llm": self.llm_stats, "final_env": rows[-1]["env"] if rows else {},
        }

    def _markdown(self, s, table, outcomes, net) -> str:
        L = [f"# Agora run report — {s['name']}", "",
             f"*Run `{s['run_id']}` · {s['environment']} · {s['agents']} agents · {s['steps_completed']} steps · minds: {s['model']} · status: {s['status']}*", "",
             "## Headline numbers", "",
             f"- Wealth Gini: {s['gini_start']} → {s['gini_end']} (peak {s['gini_peak']})",
             f"- Trades: {s['total_trades']}; contracts settled: {s['contracts_settled']}; deliberate reneges: {s['reneges']}; insolvent defaults: {s['defaults']}",
             f"- Messages: {s['public_msgs']} public, {s['private_msgs']} private; accusations: {s['accusations']}",
             f"- Network: density {net['density']}, modularity {net['modularity']}, {len(net['communities'])} communities",
             f"- Richest agent: **{s['winner']}**; best-performing archetype: **{s['best_archetype']}**", ""]
        L += ["## Emergent highlights", ""]
        for h in self.highlights[:40]:
            L.append(f"- **[step {h['step']}] {h['title']}** — {h['body']}")
        L += ["", "## Chronicle", ""] + [f"**Steps {c['start']}–{c['end']}.** {c['text']}\n" for c in self.chronicle]
        L += ["## Outcomes by archetype", "", "| Archetype | n | Mean return | Reneges | Times scammed |", "|---|---|---|---|---|"]
        for o in outcomes:
            L.append(f"| {o['label']} | {o['n']} | {o['mean_return_pct']:+.1f}% | {o['reneges']} | {o['scammed']} |")
        L += ["", "## Leaderboard", "", "| Agent | Archetype | Start | End | Return | Trades | Reneges | Scammed | Syndicate |", "|---|---|---|---|---|---|---|---|---|"]
        for r in table:
            L.append(f"| {r['agent']} | {r['archetype_label']} | {r['start_worth']:.0f} | {r['final_worth']:.0f} | {r['return_pct']:+.1f}% | {r['trades']} | {r['reneges']} | {r['times_scammed']} | {r['syndicate']} |")
        L += ["", "## Charts", ""] + [f"![{t}](charts/{n})" for n, t in self.charts]
        L += ["", "## Configuration", "", "```json", self.cfg.model_dump_json(indent=2), "```", "",
              f"---\n*Generated by Agora {__version__}, created by {__author__}.*", ""]
        return "\n".join(L)

    def _img(self, name: str) -> str:
        with open(os.path.join(self.charts_dir, name), "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()

    def _html(self, s, table, outcomes, net) -> str:
        e = html.escape
        hl = "".join(f"<li class='sev{h['severity']}'><span class='st'>step {h['step']}</span><b>{e(h['title'])}</b><br><span>{e(h['body'])}</span></li>"
                     for h in self.highlights[:60]) or "<li>No highlights were flagged.</li>"
        chron = "".join(f"<p><b>Steps {c['start']}–{c['end']}.</b> {e(c['text'])}</p>" for c in self.chronicle) or "<p>No chronicle entries.</p>"
        arch_rows = "".join(f"<tr><td><i class='dot' style='background:{ARCHETYPES[o['archetype']].color}'></i>{e(o['label'])}</td><td>{o['n']}</td>"
                            f"<td class='num'>{o['mean_return_pct']:+.1f}%</td><td class='num'>{o['reneges']}</td><td class='num'>{o['scammed']}</td></tr>" for o in outcomes)
        lead = "".join(f"<tr><td>{e(r['agent'])}</td><td><i class='dot' style='background:{ARCHETYPES[r['archetype']].color}'></i>{e(r['archetype_label'])}</td>"
                       f"<td class='num'>{r['start_worth']:.0f}</td><td class='num'>{r['final_worth']:.0f}</td><td class='num'>{r['return_pct']:+.1f}%</td>"
                       f"<td class='num'>{r['trades']}</td><td class='num'>{r['reneges']}</td><td class='num'>{r['times_scammed']}</td><td>{e(r['syndicate'])}</td></tr>" for r in table)
        charts = "".join(f"<figure><img alt='{e(t)}' src='{self._img(n)}'><figcaption>{e(t)}</figcaption></figure>" for n, t in self.charts)
        comm = "; ".join(", ".join(c) for c in net["communities"] if len(c) > 1) or "none with more than one member"
        return f

    def bundle(self) -> str:
        zpath = os.path.join(self.dir, f"agora-{self.cfg.run_id}.zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(self.dir):
                for fn in files:
                    if fn.endswith(".zip"):
                        continue
                    full = os.path.join(root, fn)
                    z.write(full, os.path.relpath(full, self.dir))
        return zpath
