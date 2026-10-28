import random

import pytest

from agora.agents.memory import AgentMemory
from agora.agents.persona import ARCHETYPES, allocate_archetypes, generate_personas
from agora.agents.prompts import extract_json
from agora.core.actions import ActionParseError, normalize_action, normalize_actions
from agora.core.ledger import ESCROW, WORLD, InsufficientFunds, Ledger
from agora.core.money import to_cents
from agora.environments.base import Notice
from agora.observe.metrics import gini, hhi, lorenz, theil, top_share


# ------------------------------------------------------------------ ledger
def test_ledger_conservation_random_walk():
    rng = random.Random(0)
    L = Ledger()
    ids = [f"a{i}" for i in range(6)]
    for a in ids:
        L.open(a)
        L.mint(a, cash=rng.randint(0, 10_000), items={"x": rng.randint(0, 20), "y": rng.randint(0, 5)})
    for _ in range(3000):
        s, d = rng.sample(ids + [ESCROW], 2)
        c = rng.randint(0, 3000)
        items = {"x": rng.randint(0, 4)} if rng.random() < 0.5 else {"y": rng.randint(0, 2)}
        try:
            L.transfer(s, d, cash=c, items=items)
        except InsufficientFunds:
            pass
        if rng.random() < 0.05:
            a = rng.choice(ids)
            try:
                L.burn(a, cash=rng.randint(0, 100))
            except InsufficientFunds:
                pass
        L.check_invariants()


def test_ledger_atomic_failure():
    L = Ledger()
    L.open("a"); L.open("b")
    L.mint("a", cash=100, items={"g": 1})
    with pytest.raises(InsufficientFunds):
        L.transfer("a", "b", cash=50, items={"g": 2})
    assert L.cash("a") == 100 and L.qty("a", "g") == 1 and L.cash("b") == 0
    with pytest.raises(ValueError):
        L.transfer("a", "b", cash=-1)
    assert L.cash(WORLD) == -100


def test_to_cents():
    assert to_cents("1,234.567") == 123457
    assert to_cents("$5") == 500
    with pytest.raises(ValueError):
        to_cents(float("nan"))


# ------------------------------------------------------------------ actions
def test_action_aliases_and_bundles():
    a = normalize_action({"type": "Sell", "item": "Grain", "qty": "3", "price": "6.5"})
    assert a.type == "post_order" and a.side == "sell" and a.item == "grain" and a.qty == 3 and a.price == 650
    c = normalize_action({"type": "offer", "to": "Mira", "give": {"cash": 10, "items": {"ore": 2}},
                          "want": {"items": [{"name": "bread", "qty": 1}]}, "settlement": "Trust", "delay": "3"})
    assert c.type == "propose_contract" and c.target == "Mira" and c.settlement == "trust" and c.delay == 3
    assert c.offer.cash == 1000 and c.offer.items == {"ore": 2} and c.request.items == {"bread": 1}
    v = normalize_action({"type": "cast_vote", "proposal": "P3", "vote": "Aye"})
    assert v.type == "vote" and v.vote is True and v.proposal_id == "P3"
    h = normalize_action({"type": "fish", "amount": "12.5"})
    assert h.type == "harvest" and h.harvest == 12.5


def test_action_errors_and_limits():
    with pytest.raises(ActionParseError):
        normalize_action({"type": "post_order", "qty": -2})
    with pytest.raises(ActionParseError):
        normalize_action({"qty": 1})
    acts, errs = normalize_actions([{"type": "pass"}] * 6, limit=4)
    assert len(acts) == 4 and errs
    acts, errs = normalize_actions("nonsense", limit=4)
    assert acts == [] and errs


# ------------------------------------------------------------------ metrics
def test_gini_known_values():
    assert gini([5, 5, 5, 5]) == pytest.approx(0.0)
    assert gini([0, 0, 0, 10]) == pytest.approx(0.75)  # (n-1)/n
    assert gini([1, 2, 3, 4, 5]) == pytest.approx(4 / 15)
    assert gini([]) == 0.0


def test_theil_topshare_lorenz_hhi():
    assert theil([3, 3, 3]) == pytest.approx(0.0)
    assert theil([0, 0, 1]) == pytest.approx(1.0986, abs=1e-3)  # ln n
    assert top_share([1, 1, 1, 1, 6], 0.2) == pytest.approx(0.6)
    x, y = lorenz([1, 1, 2])
    assert x[0] == 0 and y[-1] == pytest.approx(1.0) and y[1] == pytest.approx(0.25)
    assert hhi([1, 1, 1, 1]) == pytest.approx(0.25)
    assert hhi([1, 0, 0]) == pytest.approx(1.0)


# ------------------------------------------------------------------ personas & memory
def test_allocation_exact_and_weighted():
    rng = random.Random(1)
    kinds = allocate_archetypes(20, {"trickster": 1, "steward": 3}, rng)
    assert len(kinds) == 20 and kinds.count("steward") == 15 and kinds.count("trickster") == 5
    ps = generate_personas(20, "marketplace", 3)
    assert len({p.id for p in ps}) == 20
    assert all(0 < v < 1 for p in ps for v in p.traits.values())
    assert all(0.2 <= p.temperature <= 1.4 for p in ps)
    assert set(p.archetype for p in ps) <= set(ARCHETYPES)


def test_trust_is_asymmetric():
    p = generate_personas(1, "marketplace", 2, {"enforcer": 1})[0]
    m = AgentMemory(p)
    m.update_trust("X", 0.5, 1)
    up = m.trust_of("X")
    m.update_trust("X", -1.0, 2)
    assert m.trust_of("X") < 0 < up
    m.ingest([Notice(3, "betrayed", "X reneged", other="X", valence=-1.0, magnitude=10)], 3)
    assert m.grievances and m.grievances[-1].other == "X"
    assert "grievances" in m.render()


# ------------------------------------------------------------------ JSON extraction
@pytest.mark.parametrize("text", [
    '{"thought":"a","actions":[]}',
    'Sure!\n```json\n{"thought":"a","actions":[]}\n```',
    'blah {"thought":"a {b}","actions":[{"type":"pass"},]} trailing',
])
def test_extract_json(text):
    obj, err = extract_json(text)
    assert err is None and obj["thought"].startswith("a")


def test_extract_json_failure():
    obj, err = extract_json("I will buy grain.")
    assert obj is None and err
