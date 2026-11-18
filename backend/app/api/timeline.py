import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.common import get_or_404
from app.core.db import get_db
from app.core.deps import client_ip, get_current_user
from app.models import Account, Case, User
from app.services.audit import audit
from app.services.timeline import build_timeline

router = APIRouter(prefix="/timeline", tags=["timeline"])


def _params(case_id: int | None = None, account_id: int | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
            kinds: str | None = None, flagged_only: bool = False, limit: int = Query(2000, ge=1, le=10000)):
    return dict(case_id=case_id, account_id=account_id, date_from=date_from, date_to=date_to,
                kinds=kinds.split(",") if kinds else None, flagged_only=flagged_only, limit=limit)


def _check(db, p):
    if p["case_id"] is None and p["account_id"] is None:
        raise HTTPException(422, "Provide case_id or account_id")
    if p["case_id"] is not None:
        get_or_404(db, Case, p["case_id"], "Case")
    if p["account_id"] is not None:
        get_or_404(db, Account, p["account_id"], "Account")


@router.get("")
def timeline(p: dict = Depends(_params), db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    _check(db, p)
    return build_timeline(db, **p)


@router.get("/export")
def export(request: Request, format: str = "csv", p: dict = Depends(_params), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _check(db, p)
    t = build_timeline(db, **p)
    audit(db, user, "timeline.exported", "case" if p["case_id"] else "account", p["case_id"] or p["account_id"], {"format": format}, client_ip(request))
    db.commit()
    if format == "json":
        return Response(json.dumps(t, indent=1), media_type="application/json", headers={"Content-Disposition": "attachment; filename=timeline.json"})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp", "kind", "title", "description", "amount", "risk_score", "severity", "reference"])
    for e in t["events"]:
        w.writerow([e["timestamp"], e["kind"], e["title"], e.get("description", ""), e.get("amount", ""), e.get("risk_score", ""),
                    e.get("severity", ""), json.dumps(e.get("ref", {}))])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=timeline.csv"})
