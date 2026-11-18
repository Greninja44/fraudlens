import io
import json
from datetime import datetime

import networkx as nx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.common import get_or_404
from app.core.db import get_db
from app.core.deps import client_ip, get_current_user
from app.models import Account, User
from app.services import graph
from app.services.audit import audit

router = APIRouter(prefix="/graph", tags=["graph"])


def _params(account_id: int | None = None, depth: int = Query(1, ge=1, le=4), date_from: datetime | None = None,
            date_to: datetime | None = None, min_amount: float | None = Query(None, ge=0), min_risk: float | None = Query(None, ge=0, le=100),
            suspicious_only: bool = False, include_entities: bool = True, limit: int = Query(800, ge=10, le=4000)):
    return dict(focus_account=account_id, depth=depth, date_from=date_from, date_to=date_to, min_amount=min_amount,
                min_risk=min_risk, suspicious_only=suspicious_only, include_entities=include_entities, limit=limit)


@router.get("")
def get_graph(p: dict = Depends(_params), db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    if p["focus_account"] is not None:
        get_or_404(db, Account, p["focus_account"], "Account")
    return graph.build_graph(db, **p)


@router.get("/path")
def path(source: int, target: int, directed: bool = True, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    get_or_404(db, Account, source, "Source account")
    get_or_404(db, Account, target, "Target account")
    if source == target:
        raise HTTPException(422, "Source and target must differ")
    return graph.shortest_path(db, source, target, directed)


@router.get("/components")
def components(min_risk: float = Query(50, ge=0, le=100), db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return graph.components_summary(db, min_risk)


@router.get("/cycles")
def cycles(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return graph.circular_flows(db)


@router.get("/export")
def export(request: Request, format: str = "json", p: dict = Depends(_params), db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    g = graph.build_graph(db, **p)
    audit(db, user, "graph.exported", "graph", None, {"format": format, "focus": p["focus_account"]}, client_ip(request))
    db.commit()
    if format == "json":
        return Response(json.dumps(g, indent=1), media_type="application/json",
                        headers={"Content-Disposition": "attachment; filename=financial_network.json"})
    if format == "graphml":
        G = nx.MultiDiGraph()
        for n in g["nodes"]:
            G.add_node(n["id"], **{k: (v if isinstance(v, (int, float, str)) else json.dumps(v)) for k, v in n.items() if v is not None and k != "id"})
        for e in g["edges"]:
            G.add_edge(e["source"], e["target"], **{k: v for k, v in e.items() if v is not None and k not in ("source", "target")})
        buf = io.BytesIO()
        nx.write_graphml(G, buf)
        return Response(buf.getvalue(), media_type="application/xml", headers={"Content-Disposition": "attachment; filename=financial_network.graphml"})
    raise HTTPException(422, "format must be json or graphml")
