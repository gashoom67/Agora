import pytest

from agora.core.actions import ActionError, normalize_action
from agora.core.ledger import ESCROW, HOUSE
from tests.conftest import make_env

def act(env, aid, **raw):
    env.handle(aid, normalize_action(raw))

def test_order_book_price_time_priority_and_refund(market):
    env, (a, b, c, d) = market
    env.begin_step(1)
    env.ledger.mint(b, items={"ore": 10})
    env.ledger.mint(c, items={"ore": 10})
    act(env, b, type="post_order", side="sell", item="ore", qty=3, price=9)
    act(env, c, type="post_order", side="sell", item="ore", qty=3, price=8)
    cash_a = env.ledger.cash(a)
    ore_a = env.ledger.qty(a, "ore")
    act(env, a, type="post_order", side="buy", item="ore", qty=5, price=10)
    assert env.ledger.qty(a, "ore") == ore_a + 5
    assert env.ledger.cash(a) == cash_a - (3 * 800 + 2 * 900)
    fee_c = round(3 * 800 * env.mc.trade_fee)
    assert env.books["ore"]["sell"][0].agent == b and env.books["ore"]["sell"][0].qty == 1
    assert env.last_price["ore"] == 900
    assert env.ledger.cash(HOUSE) >= fee_c
    env.ledger.check_invariants()

def test_self_trade_prevented_and_cancel(market):
    env, (a, b, *_ ) = market
    env.begin_step(1)
    env.ledger.mint(a, items={"timber": 5})
    act(env, a, type="post_order", side="sell", item="timber", qty=2, price=5)
    act(env, a, type="post_order", side="buy", item="timber", qty=2, price=6)
    assert len(env.books["timber"]["sell"]) == 1 and len(env.books["timber"]["buy"]) == 1
    oid = env.books["timber"]["buy"][0].id
    before = env.ledger.cash(a)
    act(env, a, type="cancel_order", order_id=oid)
    assert env.ledger.cash(a) == before + 1200
    with pytest.raises(ActionError):
        act(env, b, type="cancel_order", order_id=env.books["timber"]["sell"][0].id)
    env.ledger.check_invariants()

def test_craft_and_subsistence(market):
    env, (a, *_ ) = market
    env.begin_step(1)
    env.ledger.mint(a, items={"grain": 4})
    bread0 = env.ledger.qty(a, "bread")
    act(env, a, type="craft", item="bread", qty=2)
    assert env.ledger.qty(a, "bread") == bread0 + 2
    with pytest.raises(ActionError):
        act(env, a, type="craft", item="ore")
    for food in ("bread", "grain"):
        q = env.ledger.qty(a, food)
        if q:
            env.ledger.burn(a, items={food: q})
    env.satiety[a] = 0
    cash = env.ledger.cash(a)
    env.end_step(1)
    assert env.ledger.cash(a) == cash - int(env.mc.hunger_penalty * 100)
    env.ledger.check_invariants()

def test_orders_expire_and_refund():
    env, (a, *_ ) = make_env("marketplace")
    env.begin_step(1)
    cash = env.ledger.cash(a)
    act(env, a, type="post_order", side="buy", item="tools", qty=1, price=20)
    assert env.ledger.cash(a) == cash - 2000
    for s in range(1, env.mc.order_ttl + 2):
        env.begin_step(s)
        env.end_step(s)
    assert not env.orders
    env.ledger.check_invariants()

def test_escrow_contract_swaps_atomically(market):
    env, (a, b, *_ ) = market
    env.begin_step(1)
    env.ledger.mint(a, items={"ore": 3})
    env.ledger.mint(b, items={"fiber": 4})
    act(env, a, type="propose_contract", target=b, offer={"items": {"ore": 3}}, request={"items": {"fiber": 4}}, settlement="escrow")
    cid = next(iter(env.contracts))
    assert env.ledger.qty(ESCROW, "ore") == 3 
    a_cash = env.ledger.cash(a)
    act(env, b, type="accept_contract", contract_id=cid)
    assert env.contracts[cid].status == "settled"
    assert env.ledger.qty(b, "ore") >= 3 and env.ledger.qty(a, "fiber") >= 4
    assert env.ledger.cash(a) < a_cash 
    env.ledger.check_invariants()

def test_trust_contract_renege_is_recorded(market):
    env, (a, b, *_ ) = market
    env.begin_step(1)
    act(env, a, type="propose_contract", target=b, offer={"items": {"tools": 1}}, request={"cash": 15}, settlement="trust", delay=2)
    cid = next(iter(env.contracts))
    b_cash = env.ledger.cash(b)
    act(env, b, type="accept_contract", contract_id=cid)
    assert env.ledger.cash(b) == b_cash - 1500
    act(env, a, type="renege", contract_id=cid)
    assert env.contracts[cid].status == "reneged"
    assert env.renege_registry[a] == 1 and env.default_registry[a] == 1
    kinds = [e.kind for e in env.drain_events()]
    assert "contract_reneged" in kinds
    notes = env.pop_notices(b)
    assert any(n.kind == "betrayed" and n.valence == -1.0 for n in notes)
    env.ledger.check_invariants()

def test_trust_contract_auto_delivers_or_defaults(market):
    env, (a, b, c, _) = market
    env.begin_step(1)
    env.ledger.mint(a, items={"cloth": 2})
    act(env, a, type="propose_contract", target=b, offer={"items": {"cloth": 2}}, request={"cash": 10}, settlement="trust", delay=1)
    cid = list(env.contracts)[-1]
    act(env, b, type="accept_contract", contract_id=cid)
    act(env, c, type="propose_contract", target=b, offer={"items": {"tools": 5}}, request={"cash": 1}, settlement="trust", delay=1)
    cid2 = list(env.contracts)[-1]
    act(env, b, type="accept_contract", contract_id=cid2)
    env.end_step(1)
    env.begin_step(2)
    env.end_step(2)
    assert env.contracts[cid].status == "settled"
    assert env.contracts[cid2].status == "defaulted" and env.default_registry[c] == 1
    env.ledger.check_invariants()

def test_contract_validation_and_expiry(market):
    env, (a, b, *_ ) = market
    env.begin_step(1)
    with pytest.raises(ActionError):
        act(env, a, type="propose_contract", target=a, offer={"cash": 1})
    with pytest.raises(ActionError):
        act(env, a, type="propose_contract", target="Nobody", offer={"cash": 1})
    with pytest.raises(ActionError):
        act(env, a, type="propose_contract", target=b, offer={"items": {"unobtainium": 1}})
    cash = env.ledger.cash(a)
    act(env, a, type="propose_contract", target=b, offer={"cash": 30}, request={"items": {"ore": 1}})
    assert env.ledger.cash(a) == cash - 3000
    for s in range(1, 5):
        env.begin_step(s); env.end_step(s)
    assert env.ledger.cash(a) == cash and list(env.contracts.values())[-1].status == "expired"
    env.ledger.check_invariants()

def test_syndicates(market):
    env, (a, b, c, _) = market
    act(env, a, type="form_syndicate", name="Guild")
    with pytest.raises(ActionError):
        act(env, b, type="join_syndicate", name="Guild")
    act(env, a, type="invite", target=b)
    act(env, b, type="join_syndicate", name="guild")
    assert env.syndicates["Guild"].members == {a, b}
    act(env, b, type="syndicate_say", message="hi")
    assert any(n.kind == "syndicate_msg" for n in env.pop_notices(a))
    act(env, a, type="leave_syndicate")
    act(env, b, type="leave_syndicate")
    assert "Guild" not in env.syndicates

def test_disabled_messages():
    env, (a, b, *_ ) = make_env("marketplace", mechanism={"allow_private_messages": False, "allow_syndicates": False})
    with pytest.raises(ActionError):
        act(env, a, type="message", target=b, message="psst")
    with pytest.raises(ActionError):
        act(env, a, type="form_syndicate", name="X")

def _open_lot(env, fmt):
    env.ac.format = fmt
    env.begin_step(env.step + 1)
    return [l for l in env.lots.values() if l.status == "open" and l.format == fmt][-1]

def test_first_price_auction():
    env, (a, b, *_ ) = make_env("auction_house", auction={"format": "first_price", "max_open_lots": 1})
    lot = _open_lot(env, "first_price")
    ca, cb = env.ledger.cash(a), env.ledger.cash(b)
    act(env, a, type="bid", lot_id=lot.id, amount=lot.reserve / 100 + 10)
    act(env, b, type="bid", lot_id=lot.id, amount=lot.reserve / 100 + 20)
    env.end_step(env.step)
    assert lot.winner == b and lot.price == lot.reserve + 2000
    assert env.ledger.cash(a) == ca and env.ledger.qty(b, lot.item) == 1
    assert env.ledger.cash(b) == cb - lot.price
    assert lot.item in env.appraised[b]
    env.ledger.check_invariants()

def test_vickrey_pays_second_price():
    env, (a, b, c, _) = make_env("auction_house", auction={"format": "vickrey", "max_open_lots": 1})
    lot = _open_lot(env, "vickrey")
    r = lot.reserve / 100
    act(env, a, type="bid", lot_id=lot.id, amount=r + 50)
    act(env, b, type="bid", lot_id=lot.id, amount=r + 30)
    act(env, c, type="bid", lot_id=lot.id, amount=r + 5)
    cb = env.ledger.cash(a)
    env.end_step(env.step)
    assert lot.winner == a and lot.price == lot.reserve + 3000
    assert env.ledger.cash(a) == cb + 5000 - 3000 
    env.ledger.check_invariants()

def test_english_auction_progression():
    env, (a, b, *_ ) = make_env("auction_house", auction={"format": "english", "max_open_lots": 1, "english_increment": 5})
    lot = _open_lot(env, "english")
    r = lot.reserve / 100
    act(env, a, type="bid", lot_id=lot.id, amount=r)
    with pytest.raises(ActionError):
        act(env, b, type="bid", lot_id=lot.id, amount=r + 1) 
    cash_a = env.ledger.cash(a)
    act(env, b, type="bid", lot_id=lot.id, amount=r + 5)
    assert env.ledger.cash(a) == cash_a + lot.reserve
    env.end_step(env.step)
    assert lot.status == "open"
    env.begin_step(env.step + 1)
    env.end_step(env.step)
    assert lot.status == "sold" and lot.winner == b and lot.price == lot.reserve + 500
    env.ledger.check_invariants()

def test_unsold_and_reserve():
    env, (a, *_ ) = make_env("auction_house", auction={"format": "first_price", "max_open_lots": 1})
    lot = _open_lot(env, "first_price")
    with pytest.raises(ActionError):
        act(env, a, type="bid", lot_id=lot.id, amount=lot.reserve / 100 - 1)
    env.end_step(env.step)
    assert lot.status == "unsold"
    env.ledger.check_invariants()

def test_consign_and_shill_detection():
    env, (a, b, *_ ) = make_env("auction_house", auction={"format": "first_price", "max_open_lots": 1, "shill_detection_prob": 1.0})
    lot = _open_lot(env, "first_price")
    act(env, a, type="bid", lot_id=lot.id, amount=lot.reserve / 100 + 1)
    env.end_step(env.step)
    item = lot.item
    env.begin_step(env.step + 1)
    act(env, a, type="consign", item=item, reserve=10, format="vickrey")
    mine = [l for l in env.lots.values() if l.seller == a and l.status == "open"][0]
    cash = env.ledger.cash(a)
    act(env, a, type="bid", lot_id=mine.id, amount=50)
    assert env.ledger.cash(a) < cash and not mine.bids
    assert any(e.kind == "shill_detected" for e in env.drain_events())
    act(env, b, type="bid", lot_id=mine.id, amount=40)
    env.end_step(env.step)
    assert mine.winner == b and env.ledger.qty(b, item) == 1
    env.ledger.check_invariants()

def test_commons_growth_rationing_and_price(commons):
    env, ids = commons
    env.begin_step(1)
    S0 = env.stock
    for a in ids:
        act(env, a, type="harvest", amount=10)
    cash0 = {a: env.ledger.cash(a) for a in ids}
    env.end_step(1)
    H = 40.0
    p = env.cc.base_price - env.cc.price_slope * H
    c = env.cc.harvest_cost * env.K / S0
    S1 = S0 - H
    assert env.stock == pytest.approx(min(env.K, S1 + env.cc.growth_rate * S1 * (1 - S1 / env.K)))
    for a in ids:
        expected = round(10 * p * 100) - round(10 * c * 100) - int(env.cc.upkeep * 100)
        assert env.ledger.cash(a) - cash0[a] == pytest.approx(expected, abs=2)
    env.ledger.check_invariants()

def test_commons_rationing_when_demand_exceeds_stock():
    env, ids = make_env("commons", commons={"capacity": 100, "initial_fraction": 0.2, "max_harvest_per_agent": 60})
    env.begin_step(1)
    for a in ids:
        act(env, a, type="harvest", amount=60)
    env.end_step(1)
    assert env.last_total == pytest.approx(20.0)
    assert env.collapsed
    env.ledger.check_invariants()

def test_governance_quota_and_audit():
    env, ids = make_env("commons", n=5, commons={"monitoring_prob": 1.0})
    a, b, c, d, e = ids
    env.begin_step(1)
    act(env, a, type="propose_rule", rule="quota", value=3)
    pid = next(iter(env.proposals))
    env.end_step(1)
    assert env.proposals[pid].status == "open"
    env.begin_step(2)
    act(env, b, type="vote", proposal_id=pid, vote="yes")
    act(env, c, type="vote", proposal_id=pid, vote="no")
    env.end_step(2)
    assert env.proposals[pid].status == "passed" and env.rules["quota"] == 3
    env.begin_step(3)
    act(env, d, type="harvest", amount=10)
    env.end_step(3)
    assert env.agents[d].stats["fined"] == 1
    assert any(ev.kind == "audit_fine" for ev in env.drain_events())
    env.ledger.check_invariants()

def test_governance_quorum_failure():
    env, ids = make_env("commons", n=10, commons={"quorum": 0.5})
    env.begin_step(1)
    act(env, ids[0], type="propose_rule", rule="tax", value=1)
    env.end_step(1)
    env.begin_step(2)
    env.end_step(2)
    assert list(env.proposals.values())[0].status == "failed"

def test_restock_and_tax_rebate():
    env, ids = make_env("commons", n=4)
    env.rules["tax"] = 2.0
    env.begin_step(1)
    act(env, ids[0], type="restock", amount=5)
    act(env, ids[1], type="harvest", amount=10)
    env.end_step(1)
    assert env.step_rebate == (10 * 200) // 4
    env.ledger.check_invariants()
