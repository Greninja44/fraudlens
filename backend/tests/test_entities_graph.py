import io

import pytest
from sqlalchemy import select

from app.detection import engine
from app.models import Account, Entity, EntityLink
from app.services import entities, graph, ingestion

CSV = """transaction_id,sender_account,sender_name,receiver_account,receiver_name,amount,timestamp,ip_address
T1,A1,John Carter,B1,Mega Store Inc,50,2025-01-01T10:00:00Z,203.0.113.1
T2,A2,John Carter,B1,Mega Store Inc,60,2025-01-02T10:00:00Z,203.0.113.1
T3,A3,Jon Carter,B1,Mega Store Inc,70,2025-01-03T10:00:00Z,198.51.100.7
T4,A4,Victor Hale,C1,Shell One LLC,50000,2025-02-01T10:00:00Z,203.0.113.99
T5,C1,Shell One LLC,C2,Shell Two Ltd,49000,2025-02-02T10:00:00Z,203.0.113.50
T6,C2,Shell Two Ltd,A5,V. Hale,48000,2025-02-03T10:00:00Z,203.0.113.51
T7,A5,V. Hale,A4,Victor Hale,47000,2025-02-04T10:00:00Z,203.0.113.99
T8,A5,V. Hale,D1,Dana Fox,10,2025-02-05T10:00:00Z,203.0.113.99
"""


@pytest.fixture()
def data(db):
    b = ingestion.stage_upload(db, io.BytesIO(CSV.encode()), "e.csv", 10**6, None)
    db.commit()
    ingestion.run_import(db, b, b.column_mapping)
    engine.run_detection(db, engine.start_run(db, None))
    entities.compute_links(db)
    acct = {a.account_number: a for a in db.scalars(select(Account))}
    return db, acct


def link_between(db, acct, x, y):
    a, b = sorted([acct[x].entity_id, acct[y].entity_id])
    return db.scalar(select(EntityLink).where(EntityLink.entity_a_id == a, EntityLink.entity_b_id == b))


def test_one_entity_per_account_never_auto_merged(data):
    db, acct = data
    assert acct["A1"].entity_id != acct["A2"].entity_id
    assert db.scalar(select(Entity).where(Entity.merged_into_id.isnot(None))) is None


def test_name_plus_shared_ip_is_possible_duplicate(data):
    db, acct = data
    l = link_between(db, acct, "A1", "A2")
    sigs = {s["signal"] for s in l.signals}
    assert l.link_type == "possible_duplicate" and {"exact_name", "shared_ip"} <= sigs and l.status == "suggested"
    alias = link_between(db, acct, "A4", "A5")
    assert alias and {"initial_name", "shared_ip"} <= {s["signal"] for s in alias.signals}


def test_similar_name_alone_is_capped_low_confidence(data):
    db, acct = data
    l = link_between(db, acct, "A1", "A3")  # John vs Jon Carter, different IPs
    if l is not None:
        assert l.confidence <= 0.35 and {s["signal"] for s in l.signals} <= entities.NAME_SIGNALS


def test_merge_requires_verified_link(data):
    db, acct = data
    l = link_between(db, acct, "A1", "A2")
    with pytest.raises(ValueError):
        entities.merge_entities(db, l, l.entity_a_id, 1)
    l.status = "verified"
    kept = entities.merge_entities(db, l, l.entity_a_id, 1)
    db.commit()
    assert {a.entity_id for a in db.scalars(select(Account).where(Account.account_number.in_(["A1", "A2"])))} == {kept.id}
    assert "merged_from" in kept.attributes


def test_recompute_preserves_investigator_decisions(data):
    db, acct = data
    l = link_between(db, acct, "A1", "A2")
    l.status = "rejected"
    db.commit()
    entities.compute_links(db)
    assert db.get(EntityLink, l.id).status == "rejected"


def test_shortest_path_directed_and_undirected(data):
    db, acct = data
    p = graph.shortest_path(db, acct["A4"].id, acct["A5"].id)
    assert p["found"] and [x["account_number"] for x in p["path"]] == ["A4", "C1", "C2", "A5"]
    none = graph.shortest_path(db, acct["D1"].id, acct["A4"].id, directed=True)
    assert not none["found"]
    und = graph.shortest_path(db, acct["D1"].id, acct["A4"].id, directed=False)
    assert und["found"] and und["length"] == 2


def test_graph_focus_and_filters(data):
    db, acct = data
    g = graph.build_graph(db, focus_account=acct["C1"].id, depth=1)
    nums = {n["label"] for n in g["nodes"] if n["type"] == "account"}
    assert nums == {"A4", "C1", "C2"}
    assert any(e["type"] == "owns" for e in g["edges"])
    big = graph.build_graph(db, min_amount=1000)
    assert all(e["total"] >= 1000 for e in big["edges"] if e["type"] == "transfer")


def test_cycle_and_money_flow(data):
    db, acct = data
    cyc = graph.circular_flows(db)
    assert cyc and [p["account_number"] for p in cyc[0]["path"]] == ["A4", "C1", "C2", "A5", "A4"]
    flow = graph.money_flow(db, acct["C1"].id, depth=2)
    down = {(f["from"], f["to"]) for f in flow["downstream"]}
    assert (acct["C1"].id, acct["C2"].id) in down and (acct["C2"].id, acct["A5"].id) in down
    up = {(f["from"], f["to"]) for f in flow["upstream"]}
    assert (acct["C1"].id, acct["A4"].id) in up


def test_components(data):
    db, _ = data
    comps = graph.components_summary(db, min_risk=0)
    assert comps and comps[0]["size"] >= 4
