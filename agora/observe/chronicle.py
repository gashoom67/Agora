from __future__ import annotations

from typing import List, Optional

from agora.llm.ollama import LLMError, OllamaClient
from agora.observe.detectors import Highlight

NARRATOR_SYSTEM = (
    "You are the chronicler of an economics experiment populated by autonomous agents. "
    "Write a short factual dispatch (2–4 sentences, past tense, vivid but precise) using ONLY the facts provided. "
    "Do not invent names, numbers or events. No headings, no bullet points, no preamble."
)

def facts_for_window(start: int, end: int, rows: List[dict], highlights: List[Highlight], leaders: List[tuple]) -> List[str]:
    window = [r for r in rows if start <= r["step"] <= end]
    facts: List[str] = []
    if window:
        g0, g1 = window[0]["gini"], window[-1]["gini"]
        facts.append(f"Wealth Gini moved from {g0:.2f} to {g1:.2f}.")
        trades = sum(r["trades"] for r in window)
        value = sum(r["trade_value"] for r in window)
        if trades or value:
            facts.append(f"{trades} market trades and {sum(r['contracts_settled'] for r in window)} settled contracts moved {value:.0f} coins.")
        reneges = sum(r["reneges"] for r in window)
        if reneges:
            facts.append(f"{reneges} contract(s) were deliberately broken.")
        msgs = sum(r["public_msgs"] + r["private_msgs"] for r in window)
        facts.append(f"Agents exchanged {msgs} messages.")
        em = window[-1].get("env") or {}
        if "stock_fraction" in em:
            facts.append(f"The fish stock stands at {em['stock_fraction']:.0%} of capacity; last catch {em['harvest']:.0f}.")
        if "lots_sold" in em:
            r = em.get("mean_price_to_value")
            facts.append(f"{em['lots_sold']} lots sold so far" + (f"; recent winners paid {r:.0%} of true value on average." if r else "."))
        if "prices" in em:
            facts.append("Reference prices: " + ", ".join(f"{g} {p:.2f}" for g, p in em["prices"].items()) + ".")
    for h in highlights[:6]:
        body = h.body.strip()
        if body and body[-1] not in ".!?":
            body += "."
        facts.append(f"{h.title}. {body}")
    if leaders:
        facts.append("Richest now: " + ", ".join(f"{a} ({w:.0f})" for a, w in leaders[:3]) + ".")
    return facts

def template_summary(start: int, end: int, facts: List[str]) -> str:
    return f"Steps {start}–{end}: " + " ".join(facts[:6])

async def narrate(start: int, end: int, facts: List[str], client: Optional[OllamaClient], model: Optional[str]) -> tuple:
    
    if client is None or not model:
        return template_summary(start, end, facts), "template"
    msg = f"Window: steps {start} to {end}.\nFacts:\n- " + "\n- ".join(facts)
    try:
        resp = await client.chat(model, [{"role": "system", "content": NARRATOR_SYSTEM}, {"role": "user", "content": msg}],
                                 schema=None, temperature=0.4, num_predict=220)
        text = resp.content.strip()
        if len(text) < 20:
            raise LLMError("narrator returned too little text")
        return text, "llm"
    except LLMError:
        return template_summary(start, end, facts), "template"
