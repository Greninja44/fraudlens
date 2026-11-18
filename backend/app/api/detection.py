from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from app.api.common import ACCOUNT_FIELDS, ALERT_FIELDS, get_or_404, page_params, row
from app.core import db as dbmod
from app.core.db import get_db
from app.core.deps import client_ip, get_current_user, require
from app.detection import engine
from app.detection.rules import PATTERN_LABELS
from app.models import Account, Alert, DetectionRun, Transaction, User, utcnow
from app.services.audit import audit
from app.services.graph import latest_run_id

router = APIRouter(tags=["detection"])
RUN_FIELDS = ["id", "status", "params", "stats", "evaluation", "error", "triggered_by_id", "started_at", "completed_at"]


@router.get("/detection/patterns")
def patterns(_: User = Depends(get_current_user)):
    return PATTERN_LABELS


def _bg_run(run_id: int):
    engine.execute_run(dbmod.SessionLocal, run_id)


@router.post("/detection/runs", status_code=202)
def start_run(request: Request, bg: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require("detection:run"))):
    running = db.scalar(select(DetectionRun).where(DetectionRun.status == "running"))
    if running:
        started = running.started_at
        # a run stuck for > 30 min (e.g. process restart) is marked failed instead of blocking forever
        if started and (utcnow() - (started if started.tzinfo else started.replace(tzinfo=utcnow().tzinfo))).total_seconds() > 1800:
            running.status, running.error = "failed", "abandoned (timed out)"
        else:
            raise HTTPException(409, f"Detection run #{running.id} is already in progress")
    run = engine.start_run(db, user.id)
    audit(db, user, "detection.started", "detection_run", run.id, ip=client_ip(request))
    db.commit()
    bg.add_task(_bg_run, run.id)
    return row(run, RUN_FIELDS)


@router.get("/detection/runs")
def list_runs(limit: int = 20, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return [row(r, RUN_FIELDS) for r in db.scalars(select(DetectionRun).order_by(DetectionRun.id.desc()).limit(min(limit, 100)))]


@router.get("/detection/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return row(get_or_404(db, DetectionRun, run_id, "Detection run"), RUN_FIELDS)


def _alert_out(db, alerts):
    ids = {a.account_id for a in alerts if a.account_id}
    accts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(ids)))} if ids else {}
    out = []
    for a in alerts:
        d = row(a, ALERT_FIELDS)
        d["pattern_label"] = PATTERN_LABELS.get(a.pattern, a.pattern)
        acc = accts.get(a.account_id)
        d["account"] = {"id": acc.id, "account_number": acc.account_number, "holder": acc.holder_name, "risk_score": acc.risk_score} if acc else None
        out.append(d)
    return out


@router.get("/alerts")
def list_alerts(run_id: int | None = None, pattern: str | None = None, kind: str | None = None, severity: str | None = None,
                review_status: str | None = None, account_id: int | None = None, min_score: float | None = None,
                date_from: datetime | None = None, date_to: datetime | None = None, page: int = 1, page_size: int = 50,
                sort: str = "score", db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    run_id = run_id or latest_run_id(db)
    if not run_id:
        return {"total": 0, "items": [], "run_id": None}
    off, lim = page_params(page, page_size)
    q = select(Alert).where(Alert.run_id == run_id)
    if pattern:
        q = q.where(Alert.pattern == pattern)
    if kind:
        q = q.where(Alert.kind == kind)
    if severity:
        q = q.where(Alert.severity.in_(severity.split(",")))
    if review_status:
        q = q.where(Alert.review_status.in_(review_status.split(",")))
    if account_id:
        q = q.where(Alert.account_id == account_id)
    if min_score is not None:
        q = q.where(Alert.score >= min_score)
    if date_from:
        q = q.where(Alert.last_txn_at >= date_from)
    if date_to:
        q = q.where(Alert.first_txn_at <= date_to)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    order = {"score": Alert.score.desc(), "recent": Alert.last_txn_at.desc(), "amount": Alert.total_amount.desc()}.get(sort, Alert.score.desc())
    alerts = db.scalars(q.order_by(order, Alert.id).offset(off).limit(lim)).all()
    return {"total": total, "items": _alert_out(db, alerts), "run_id": run_id}


@router.get("/alerts/summary")
def alerts_summary(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    run_id = latest_run_id(db)
    if not run_id:
        return {"run_id": None, "by_pattern": [], "by_severity": {}, "by_review": {}}
    by_pattern = db.execute(select(Alert.pattern, Alert.kind, func.count(), func.sum(Alert.total_amount)).where(Alert.run_id == run_id)
                            .group_by(Alert.pattern, Alert.kind)).all()
    by_sev = dict(db.execute(select(Alert.severity, func.count()).where(Alert.run_id == run_id, Alert.kind == "rule").group_by(Alert.severity)).all())
    by_review = dict(db.execute(select(Alert.review_status, func.count()).where(Alert.run_id == run_id).group_by(Alert.review_status)).all())
    return {"run_id": run_id, "by_pattern": [{"pattern": p, "label": PATTERN_LABELS.get(p, p), "kind": k, "count": n, "total_amount": float(t or 0)}
                                             for p, k, n, t in sorted(by_pattern, key=lambda x: -x[2])],
            "by_severity": by_sev, "by_review": by_review}


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    a = get_or_404(db, Alert, alert_id, "Alert")
    d = _alert_out(db, [a])[0]
    txns = db.scalars(select(Transaction).where(Transaction.id.in_(a.transaction_ids or [])).order_by(Transaction.timestamp)).all()
    accts = {x.id: x for x in db.scalars(select(Account).where(Account.id.in_({t.sender_account_id for t in txns} | {t.receiver_account_id for t in txns} | set(a.details.get("account_ids", [])))))}
    d["transactions"] = [{"id": t.id, "txn_id": t.txn_id, "timestamp": row(t, ["timestamp"])["timestamp"], "amount": float(t.amount),
                          "sender_account_id": t.sender_account_id, "receiver_account_id": t.receiver_account_id,
                          "sender": accts[t.sender_account_id].account_number, "receiver": accts[t.receiver_account_id].account_number,
                          "sender_name": t.sender_name, "receiver_name": t.receiver_name, "risk_score": t.risk_score,
                          "reference": t.reference, "ip_address": t.ip_address} for t in txns]
    d["involved_accounts"] = [row(accts[i], ["id", "account_number", "holder_name", "risk_score", "risk_level"]) for i in a.details.get("account_ids", []) if i in accts]
    return d


class ReviewIn(BaseModel):
    review_status: str
    note: str | None = None


@router.post("/alerts/{alert_id}/review")
def review_alert(alert_id: int, body: ReviewIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("alerts:review"))):
    if body.review_status not in ("new", "acknowledged", "dismissed", "escalated"):
        raise HTTPException(422, "review_status must be new|acknowledged|dismissed|escalated")
    a = get_or_404(db, Alert, alert_id, "Alert")
    old = a.review_status
    a.review_status, a.reviewed_by_id, a.reviewed_at = body.review_status, user.id, utcnow()
    audit(db, user, "alert.reviewed", "alert", a.id, {"from": old, "to": body.review_status, "note": body.note}, client_ip(request))
    db.commit()
    return _alert_out(db, [a])[0]


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    from app.models import Case, CaseStatus
    total = db.scalar(select(func.count()).select_from(Transaction)) or 0
    flagged, flagged_value = db.execute(select(func.count(), func.sum(Transaction.amount)).where(Transaction.is_flagged.is_(True))).one()
    total_value = db.scalar(select(func.sum(Transaction.amount))) or 0
    high_risk = db.scalar(select(func.count()).select_from(Account).where(Account.risk_score >= 60)) or 0
    accounts = db.scalar(select(func.count()).select_from(Account)) or 0
    active_cases = db.scalar(select(func.count()).select_from(Case).where(Case.status != CaseStatus.CLOSED)) or 0
    run_id = latest_run_id(db)
    run = db.get(DetectionRun, run_id) if run_id else None
    # daily activity (all vs flagged)
    day = func.date(Transaction.timestamp)
    activity = [{"date": str(d), "count": n, "flagged": int(f or 0), "value": float(v or 0)} for d, n, f, v in db.execute(
        select(day, func.count(), func.sum(cast(Transaction.is_flagged, Integer)), func.sum(Transaction.amount))
        .group_by(day).order_by(day)).all()]
    by_pattern = []
    if run_id:
        by_pattern = [{"pattern": p, "label": PATTERN_LABELS.get(p, p), "kind": k, "count": n} for p, k, n in db.execute(
            select(Alert.pattern, Alert.kind, func.count()).where(Alert.run_id == run_id).group_by(Alert.pattern, Alert.kind)).all()]
    recent_alerts = _alert_out(db, db.scalars(select(Alert).where(Alert.run_id == run_id, Alert.kind == "rule", Alert.severity.in_(["high", "critical"]))
                                              .order_by(Alert.last_txn_at.desc()).limit(8)).all()) if run_id else []
    recent_cases = [{"id": c.id, "case_number": c.case_number, "title": c.title, "status": c.status, "priority": c.priority,
                     "updated_at": row(c, ["updated_at"])["updated_at"]} for c in db.scalars(select(Case).order_by(Case.updated_at.desc()).limit(6))]
    risk_dist = dict(db.execute(select(Account.risk_level, func.count()).group_by(Account.risk_level)).all())
    top_accounts = [row(a, ACCOUNT_FIELDS) for a in db.scalars(select(Account).where(Account.risk_score > 0).order_by(Account.risk_score.desc()).limit(8))]
    return {
        "totals": {"transactions": total, "transaction_value": float(total_value), "flagged": flagged or 0,
                   "flagged_value": float(flagged_value or 0), "high_risk_accounts": high_risk, "accounts": accounts,
                   "active_cases": active_cases},
        "last_run": row(run, RUN_FIELDS) if run else None, "activity": activity, "by_pattern": by_pattern,
        "recent_alerts": recent_alerts, "recent_cases": recent_cases, "account_risk_distribution": risk_dist,
        "top_accounts": top_accounts,
    }
