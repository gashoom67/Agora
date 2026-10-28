from __future__ import annotations

from typing import Dict, List, Optional

import networkx as nx

from agora.core.events import Event

EDGE_KINDS = {
    "dm": (1.0, 0.05),
    "syndicate_msg": (0.5, 0.1),
    "trade": (2.0, 0.2),
    "contract_settled": (2.5, 0.4),
    "contract_accepted": (1.5, 0.2),
    "contract_reneged": (3.0, -1.0),
    "contract_defaulted": (2.0, -0.6),
    "accusation": (1.5, -0.7),
    "syndicate_joined": (1.0, 0.5),
    "lot_closed": (1.5, 0.1),
}

class SocialGraph:
    def __init__(self, agents: List[str]) -> None:
        self.g = nx.DiGraph()
        self.g.add_nodes_from(agents)

    def _add(self, a: str, b: str, kind: str, w: float, val: float, step: int) -> None:
        if a == b or a not in self.g or b not in self.g:
            return
        if not self.g.has_edge(a, b):
            self.g.add_edge(a, b, weight=0.0, valence_sum=0.0, n=0, kinds={}, last_step=step)
        e = self.g[a][b]
        e["weight"] += w
        e["valence_sum"] += val * w
        e["n"] += 1
        e["kinds"][kind] = e["kinds"].get(kind, 0) + 1
        e["last_step"] = step

    def on_event(self, ev: Event) -> None:
        spec = EDGE_KINDS.get(ev.kind)
        if not spec or not ev.actor:
            return
        w, val = spec
        targets = list(ev.targets)
        if ev.kind == "syndicate_joined":
            targets = [m for m in ev.data.get("members", []) if m != ev.actor]
        if ev.kind == "lot_closed":
            seller = ev.data.get("seller")
            targets = [seller] if seller and seller in self.g else []
        for t in targets:
            self._add(ev.actor, t, ev.kind, w, val, ev.step)

    def undirected(self, positive_only: bool = False) -> nx.Graph:
        u = nx.Graph()
        u.add_nodes_from(self.g.nodes)
        for a, b, d in self.g.edges(data=True):
            if positive_only and d["valence_sum"] < 0:
                continue
            if u.has_edge(a, b):
                u[a][b]["weight"] += d["weight"]
                u[a][b]["valence_sum"] += d["valence_sum"]
            else:
                u.add_edge(a, b, weight=d["weight"], valence_sum=d["valence_sum"])
        return u

    def communities(self) -> List[List[str]]:
        u = self.undirected(positive_only=True)
        if u.number_of_edges() == 0:
            return [[n] for n in u.nodes]
        comms = nx.algorithms.community.greedy_modularity_communities(u, weight="weight")
        return [sorted(c) for c in comms]

    def modularity(self) -> float:
        u = self.undirected(positive_only=True)
        if u.number_of_edges() == 0:
            return 0.0
        comms = self.communities()
        return float(nx.algorithms.community.modularity(u, comms, weight="weight"))

    def centrality(self) -> Dict[str, Dict[str, float]]:
        u = self.undirected()
        if u.number_of_edges() == 0:
            return {n: {"degree": 0.0, "betweenness": 0.0, "strength": 0.0} for n in u.nodes}
        deg = nx.degree_centrality(u)

        for a, b, d in u.edges(data=True):
            d["dist"] = 1.0 / max(d["weight"], 1e-6)
        btw = nx.betweenness_centrality(u, weight="dist", normalized=True)
        strength = dict(u.degree(weight="weight"))
        return {n: {"degree": round(deg[n], 4), "betweenness": round(btw[n], 4), "strength": round(strength[n], 2)} for n in u.nodes}

    def density(self) -> float:
        return float(nx.density(self.undirected()))

    def snapshot(self, node_attrs: Optional[Dict[str, dict]] = None, min_weight: float = 0.0) -> dict:
        u = self.undirected()
        nodes = []
        for n in u.nodes:
            d = {"id": n}
            if node_attrs and n in node_attrs:
                d.update(node_attrs[n])
            nodes.append(d)
        links = []
        for a, b, d in u.edges(data=True):
            if d["weight"] < min_weight:
                continue
            links.append({"source": a, "target": b, "weight": round(d["weight"], 2),
                          "valence": round(d["valence_sum"] / max(d["weight"], 1e-6), 3)})
        return {"nodes": nodes, "links": links}

    def export_graphml(self, path: str) -> None:
        g = nx.DiGraph()
        for n, d in self.g.nodes(data=True):
            g.add_node(n, **{k: v for k, v in d.items() if isinstance(v, (int, float, str, bool))})
        for a, b, d in self.g.edges(data=True):
            g.add_edge(a, b, weight=d["weight"], valence=d["valence_sum"] / max(d["weight"], 1e-6), n=d["n"],
                       kinds=",".join(f"{k}:{v}" for k, v in d["kinds"].items()))
        nx.write_graphml(g, path)
