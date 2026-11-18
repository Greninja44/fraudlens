"""Financial network construction and analysis (NetworkX).

Nodes: accounts (id "a:<id>") and holder entities (id "e:<id>", type individual|business|unknown).
Edges: aggregated account->account transfers (count, total, max risk) and entity-[owns]->account links.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import networkx as nx
from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models import Account, Alert, DetectionRun, Entity, Transaction

MAX_EDGES = 4000


def _txn_filter(date_from: datetime | None, date_to: datetime | None, min_amount: float | None, min_risk: float | None):
    conds = []
    if date_from:
        conds.append(Transaction.timestamp >= date_from)
    if date_to:
        conds.append(Transaction.timestamp <= date_to)
    if min_amount:
        conds.append(Transaction.amount >= min_amount)
    if min_risk:
        conds.append(Transaction.risk_score >= min_risk)
    return conds


def aggregated_edges(db: Session, conds: list, account_ids: set[int] | None = None, limit: int = MAX_EDGES):
    q = (select(Transaction.sender_account_id, Transaction.receiver_account_id, func.count().label("n"),
                func.sum(Transaction.amount).label("total"), func.max(Transaction.risk_score).label("max_risk"),
                func.min(Transaction.timestamp).label("first"), func.max(Transaction.timestamp).label("last"),
                func.sum(cast(Transaction.is_flagged, Integer)).label("flagged"))
         .where(*conds).group_by(Transaction.sender_account_id, Transaction.receiver_account_id))
    if account_ids is not None:
        ids = list(account_ids)
        q = q.where(or_(Transaction.sender_account_id.in_(ids), Transaction.receiver_account_id.in_(ids)))
    q = q.order_by(func.max(Transaction.risk_score).desc(), func.sum(Transaction.amount).desc()).limit(limit)
    return db.execute(q).all()


def build_graph(db: Session, *, focus_account: int | None = None, depth: int = 1, date_from=None, date_to=None,
                min_amount=None, min_risk=None, suspicious_only: bool = False, include_entities: bool = True,
                limit: int = 1500) -> dict:
    conds = _txn_filter(date_from, date_to, min_amount, min_risk)
    if suspicious_only and not min_risk:
        conds.append(Transaction.is_flagged.is_(True))
    edges_raw = []
    truncated = False
    if focus_account is not None:
        frontier, seen = {focus_account}, {focus_account}
        edge_keys = set()
        for _ in range(max(1, min(depth, 4))):
            rows = aggregated_edges(db, conds, frontier, limit=limit)
            nxt = set()
            for r in rows:
                k = (r[0], r[1])
                if k not in edge_keys:
                    edge_keys.add(k)
                    edges_raw.append(r)
                for a in (r[0], r[1]):
                    if a not in seen:
                        nxt.add(a)
                        seen.add(a)
            frontier = nxt
            if not frontier or len(edges_raw) >= limit:
                truncated = len(edges_raw) >= limit
                break
    else:
        edges_raw = aggregated_edges(db, conds, None, limit=limit + 1)
        truncated = len(edges_raw) > limit
        edges_raw = edges_raw[:limit]
    acct_ids = {r[0] for r in edges_raw} | {r[1] for r in edges_raw}
    if focus_account is not None:
        acct_ids.add(focus_account)
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(acct_ids)))} if acct_ids else {}
    ent_ids = {a.entity_id for a in accounts.values() if a.entity_id}
    entities = {e.id: e for e in db.scalars(select(Entity).where(Entity.id.in_(ent_ids)))} if ent_ids and include_entities else {}

    G = nx.DiGraph()
    for a in accounts.values():
        G.add_node(a.id)
    for r in edges_raw:
        G.add_edge(r[0], r[1])
    comp_of = {}
    for i, comp in enumerate(sorted(nx.weakly_connected_components(G), key=len, reverse=True)):
        for n in comp:
            comp_of[n] = i

    nodes = []
    for a in accounts.values():
        ent = entities.get(a.entity_id)
        nodes.append({"id": f"a:{a.id}", "type": "account", "label": a.account_number, "account_id": a.id,
                      "holder": a.holder_name, "bank": a.bank, "entity_type": ent.entity_type if ent else "unknown",
                      "risk_score": a.risk_score, "risk_level": a.risk_level, "patterns": a.patterns or [],
                      "alert_count": a.alert_count, "txn_count": a.txn_count, "component": comp_of.get(a.id),
                      "total_in": float(a.total_in or 0), "total_out": float(a.total_out or 0),
                      "is_focus": a.id == focus_account})
    edges = []
    for r in edges_raw:
        edges.append({"id": f"t:{r[0]}-{r[1]}", "source": f"a:{r[0]}", "target": f"a:{r[1]}", "type": "transfer",
                      "count": r.n, "total": float(r.total), "max_risk": float(r.max_risk or 0),
                      "flagged": int(r.flagged or 0), "first": r.first.isoformat() if r.first else None,
                      "last": r.last.isoformat() if r.last else None})
    for e in entities.values():
        nodes.append({"id": f"e:{e.id}", "type": e.entity_type, "label": e.name, "entity_id": e.id,
                      "risk_score": e.risk_score, "risk_level": None})
    for a in accounts.values():
        if a.entity_id in entities:
            edges.append({"id": f"o:{a.entity_id}-{a.id}", "source": f"e:{a.entity_id}", "target": f"a:{a.id}", "type": "owns"})
    return {"nodes": nodes, "edges": edges, "truncated": truncated,
            "stats": {"accounts": len(accounts), "entities": len(entities), "transfer_edges": len(edges_raw),
                      "components": len(set(comp_of.values()))}}


def full_digraph(db: Session, conds: list | None = None) -> nx.DiGraph:
    G = nx.DiGraph()
    for r in aggregated_edges(db, conds or [], None, limit=10**7):
        G.add_edge(r[0], r[1], count=r.n, total=float(r.total), max_risk=float(r.max_risk or 0))
    return G


def shortest_path(db: Session, source: int, target: int, directed: bool = True) -> dict:
    G = full_digraph(db)
    if source not in G or target not in G:
        return {"found": False, "reason": "One or both accounts have no transactions"}
    H = G if directed else G.to_undirected(as_view=True)
    try:
        path = nx.shortest_path(H, source, target)
    except nx.NetworkXNoPath:
        return {"found": False, "directed": directed,
                "reason": "No money-flow path in the transaction direction" if directed else "Accounts are not connected"}
    accts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(path)))}
    hops = []
    for u, v in zip(path, path[1:]):
        d = G.get_edge_data(u, v) or G.get_edge_data(v, u) or {}
        hops.append({"from": u, "to": v, "direction": "forward" if G.has_edge(u, v) else "reverse",
                     "count": d.get("count"), "total": d.get("total"), "max_risk": d.get("max_risk")})
    return {"found": True, "directed": directed, "length": len(path) - 1,
            "path": [{"account_id": a, "account_number": accts[a].account_number, "holder": accts[a].holder_name,
                      "risk_score": accts[a].risk_score} for a in path], "hops": hops}


def components_summary(db: Session, min_risk: float = 50, limit: int = 25) -> list[dict]:
    """Connected components formed by flagged transactions - candidate suspicious networks."""
    G = full_digraph(db, [Transaction.risk_score >= min_risk])
    comps = sorted(nx.weakly_connected_components(G), key=len, reverse=True)[:limit]
    ids = {n for c in comps for n in c}
    accts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(ids)))} if ids else {}
    out = []
    for i, c in enumerate(comps):
        sub = G.subgraph(c)
        total = sum(d["total"] for _, _, d in sub.edges(data=True))
        top = sorted(c, key=lambda n: accts[n].risk_score, reverse=True)[:5]
        out.append({"component": i, "size": len(c), "edges": sub.number_of_edges(), "total_value": round(total, 2),
                    "max_risk": max(accts[n].risk_score for n in c),
                    "top_accounts": [{"account_id": n, "account_number": accts[n].account_number, "holder": accts[n].holder_name,
                                      "risk_score": accts[n].risk_score} for n in top],
                    "account_ids": sorted(c)})
    return out


def latest_run_id(db: Session) -> int | None:
    return db.scalar(select(DetectionRun.id).where(DetectionRun.status == "completed").order_by(DetectionRun.id.desc()).limit(1))


def circular_flows(db: Session, limit: int = 50) -> list[dict]:
    run = latest_run_id(db)
    if not run:
        return []
    alerts = db.scalars(select(Alert).where(Alert.run_id == run, Alert.pattern == "circular").order_by(Alert.score.desc()).limit(limit)).all()
    ids = {a for al in alerts for a in al.details.get("path_account_ids", [])}
    accts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(ids)))} if ids else {}
    return [{"alert_id": al.id, "score": al.score, "confidence": al.confidence, "title": al.title,
             "transaction_ids": al.transaction_ids, "amounts": al.details.get("amounts"),
             "path": [{"account_id": a, "account_number": accts[a].account_number, "holder": accts[a].holder_name}
                      for a in al.details.get("path_account_ids", []) if a in accts]} for al in alerts]


def money_flow(db: Session, account_id: int, direction: str = "both", depth: int = 2, min_amount: float = 0,
               date_from=None, date_to=None, top: int = 15) -> dict:
    """Follow the money: where funds into an account came from (upstream) and where its outflows went
    (downstream), multi-hop, aggregated by counterparty."""
    conds = _txn_filter(date_from, date_to, min_amount or None, None)

    def hop(ids: set[int], forward: bool):
        if not ids:
            return []
        src, dst = (Transaction.sender_account_id, Transaction.receiver_account_id) if forward else \
            (Transaction.receiver_account_id, Transaction.sender_account_id)
        q = (select(src.label("frm"), dst.label("to"), func.count().label("n"), func.sum(Transaction.amount).label("total"),
                    func.max(Transaction.risk_score).label("max_risk"))
             .where(src.in_(list(ids)), *conds).group_by(src, dst)
             .order_by(func.sum(Transaction.amount).desc()))
        rows = db.execute(q).all()
        per = defaultdict(list)
        for r in rows:
            per[r.frm].append(r)
        out = []
        for frm, rs in per.items():
            out += rs[:top]
        return out

    result = {"account_id": account_id, "upstream": [], "downstream": []}
    for key, fwd in (("downstream", True), ("upstream", False)):
        if direction not in ("both", key):
            continue
        frontier, seen = {account_id}, {account_id}
        for level in range(1, max(1, min(depth, 4)) + 1):
            rows = hop(frontier, fwd)
            nxt = set()
            for r in rows:
                result[key].append({"level": level, "from": r.frm, "to": r.to, "count": r.n, "total": float(r.total),
                                    "max_risk": float(r.max_risk or 0)})
                if r.to not in seen:
                    seen.add(r.to)
                    nxt.add(r.to)
            frontier = nxt
    ids = {account_id} | {x["from"] for k in ("upstream", "downstream") for x in result[k]} | \
          {x["to"] for k in ("upstream", "downstream") for x in result[k]}
    result["accounts"] = {a.id: {"account_number": a.account_number, "holder": a.holder_name, "risk_score": a.risk_score,
                                 "risk_level": a.risk_level} for a in db.scalars(select(Account).where(Account.id.in_(ids)))}
    return result
