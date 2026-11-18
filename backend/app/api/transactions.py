import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.api.common import ACCOUNT_FIELDS, ALERT_FIELDS, TXN_FIELDS, get_or_404, jsonable, page_params, row
from app.core.db import get_db
from app.core.deps import client_ip, get_current_user
from app.models import Account, Alert, Case, CaseAccount, CaseTransaction, Entity, Transaction, User
from app.services.audit import audit
from app.services.graph import latest_run_id, money_flow

router = APIRouter(tags=["transactions"])
SORTABLE = {"timestamp": Transaction.timestamp, "amount": Transaction.amount, "risk_score": Transaction.risk_score,
            "txn_id": Transaction.txn_id}


def _filters(q, *, search, account_id, date_from, date_to, min_amount, max_amount, min_risk, flagged, pattern, txn_type,
             status, import_batch_id):
    if search:
        like = f"%{search.strip()}%"
        S, R = aliased(Account), aliased(Account)
        q = q.join(S, S.id == Transaction.sender_account_id).join(R, R.id == Transaction.receiver_account_id).where(or_(
            Transaction.txn_id.ilike(like), Transaction.sender_name.ilike(like), Transaction.receiver_name.ilike(like),
            Transaction.reference.ilike(like), S.account_number.ilike(like), R.account_number.ilike(like),
            Transaction.ip_address.ilike(like)))
    if account_id:
        q = q.where(or_(Transaction.sender_account_id == account_id, Transaction.receiver_account_id == account_id))
    if date_from:
        q = q.where(Transaction.timestamp >= date_from)
    if date_to:
        q = q.where(Transaction.timestamp <= date_to)
    if min_amount is not None:
        q = q.where(Transaction.amount >= min_amount)
    if max_amount is not None:
        q = q.where(Transaction.amount <= max_amount)
    if min_risk is not None:
        q = q.where(Transaction.risk_score >= min_risk)
    if flagged is not None:
        q = q.where(Transaction.is_flagged.is_(flagged))
    if txn_type:
        q = q.where(Transaction.txn_type == txn_type.lower())
    if status:
        q = q.where(Transaction.status == status.lower())
    if import_batch_id:
        q = q.where(Transaction.import_batch_id == import_batch_id)
    if pattern:
        # patterns is a small JSON list; match via text containment for portability across PostgreSQL/SQLite
        q = q.where(cast(Transaction.patterns, String).like(f'%"{pattern}"%'))
    return q


def _txn_list_params(search: str | None = None, account_id: int | None = None, date_from: datetime | None = None,
                     date_to: datetime | None = None, min_amount: float | None = Query(None, ge=0), max_amount: float | None = Query(None, ge=0),
                     min_risk: float | None = Query(None, ge=0, le=100), flagged: bool | None = None, pattern: str | None = None,
                     txn_type: str | None = None, status: str | None = None, import_batch_id: int | None = None):
    return dict(search=search, account_id=account_id, date_from=date_from, date_to=date_to, min_amount=min_amount,
                max_amount=max_amount, min_risk=min_risk, flagged=flagged, pattern=pattern, txn_type=txn_type, status=status,
                import_batch_id=import_batch_id)


def _with_accounts(db, txns):
    ids = {t.sender_account_id for t in txns} | {t.receiver_account_id for t in txns}
    accts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(ids)))} if ids else {}
    out = []
    for t in txns:
        d = row(t, TXN_FIELDS)
        d["sender_account"] = accts[t.sender_account_id].account_number
        d["receiver_account"] = accts[t.receiver_account_id].account_number
        out.append(d)
    return out


@router.get("/transactions")
def list_transactions(f: dict = Depends(_txn_list_params), page: int = 1, page_size: int = 50, sort: str = "timestamp",
                      order: str = "desc", db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    off, lim = page_params(page, page_size)
    q = _filters(select(Transaction), **f)
    total = db.scalar(select(func.count()).select_from(q.with_only_columns(Transaction.id).subquery()))
    col = SORTABLE.get(sort, Transaction.timestamp)
    q = q.order_by(col.desc() if order == "desc" else col.asc(), Transaction.id).offset(off).limit(lim)
    return {"total": total, "page": page, "page_size": lim, "items": _with_accounts(db, db.scalars(q).all())}


@router.get("/transactions/facets")
def facets(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    types = [r[0] for r in db.execute(select(Transaction.txn_type).distinct().order_by(Transaction.txn_type)) if r[0]]
    statuses = [r[0] for r in db.execute(select(Transaction.status).distinct().order_by(Transaction.status)) if r[0]]
    rng = db.execute(select(func.min(Transaction.timestamp), func.max(Transaction.timestamp), func.max(Transaction.amount))).one()
    return {"types": types, "statuses": statuses, "min_date": jsonable(rng[0]), "max_date": jsonable(rng[1]),
            "max_amount": float(rng[2] or 0)}


@router.get("/transactions/export")
def export_transactions(request: Request, f: dict = Depends(_txn_list_params), limit: int = Query(100000, le=500000),
                        db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = _filters(select(Transaction), **f).order_by(Transaction.timestamp).limit(limit)
    audit(db, user, "transactions.exported", "transaction", None, {k: str(v) for k, v in f.items() if v is not None}, client_ip(request))
    db.commit()

    buf = io.StringIO()
    cols = ["txn_id", "timestamp", "sender_account", "sender_name", "receiver_account", "receiver_name", "amount", "currency",
            "txn_type", "bank", "reference", "ip_address", "status", "risk_score", "risk_level", "is_flagged", "patterns", "anomaly_score"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    rows = db.scalars(q).all()
    for i in range(0, len(rows), 2000):
        for d in _with_accounts(db, rows[i:i + 2000]):
            d["patterns"] = ";".join(d["patterns"] or [])
            w.writerow(d)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=transactions.csv"})


@router.get("/transactions/{txn_pk}")
def get_transaction(txn_pk: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    t = get_or_404(db, Transaction, txn_pk, "Transaction")
    d = _with_accounts(db, [t])[0]
    d["ground_truth"] = {"label": t.ground_truth_label, "pattern": t.ground_truth_pattern,
                         "note": "Synthetic evaluation label - not an investigative finding"} if t.ground_truth_label is not None else None
    run = latest_run_id(db)
    alerts = []
    if run:
        for a in db.scalars(select(Alert).where(Alert.run_id == run, Alert.account_id.in_([t.sender_account_id, t.receiver_account_id]))):
            if t.id in (a.transaction_ids or []):
                alerts.append(row(a, ALERT_FIELDS))
    d["alerts"] = sorted(alerts, key=lambda a: -a["score"])
    d["cases"] = [{"id": c.id, "case_number": c.case_number, "title": c.title} for c in db.scalars(
        select(Case).join(CaseTransaction, CaseTransaction.case_id == Case.id).where(CaseTransaction.transaction_id == t.id))]
    return d


# ---------------------------------------------------------------- accounts
@router.get("/accounts")
def list_accounts(search: str | None = None, min_risk: float | None = None, risk_level: str | None = None, pattern: str | None = None,
                  page: int = 1, page_size: int = 50, sort: str = "risk_score", db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    off, lim = page_params(page, page_size)
    q = select(Account)
    if search:
        like = f"%{search.strip()}%"
        q = q.where(or_(Account.account_number.ilike(like), Account.holder_name.ilike(like), Account.bank.ilike(like)))
    if min_risk is not None:
        q = q.where(Account.risk_score >= min_risk)
    if risk_level:
        q = q.where(Account.risk_level == risk_level)
    if pattern:
        q = q.where(cast(Account.patterns, String).like(f'%"{pattern}"%'))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    col = {"risk_score": Account.risk_score.desc(), "txn_count": Account.txn_count.desc(), "total_in": Account.total_in.desc(),
           "total_out": Account.total_out.desc(), "account_number": Account.account_number.asc()}.get(sort, Account.risk_score.desc())
    items = db.scalars(q.order_by(col, Account.id).offset(off).limit(lim)).all()
    ents = {e.id: e for e in db.scalars(select(Entity).where(Entity.id.in_({a.entity_id for a in items if a.entity_id})))}
    out = []
    for a in items:
        d = row(a, ACCOUNT_FIELDS)
        e = ents.get(a.entity_id)
        d["entity_type"] = e.entity_type if e else None
        out.append(d)
    return {"total": total, "items": out}


@router.get("/accounts/lookup")
def lookup_account(number: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    a = db.scalar(select(Account).where(Account.account_number == number.strip().upper()))
    if not a:
        raise HTTPException(404, "Account not found")
    return row(a, ACCOUNT_FIELDS)


@router.get("/accounts/{account_id}")
def get_account(account_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    a = get_or_404(db, Account, account_id, "Account")
    d = row(a, ACCOUNT_FIELDS)
    e = db.get(Entity, a.entity_id) if a.entity_id else None
    d["entity"] = {"id": e.id, "name": e.name, "entity_type": e.entity_type, "risk_score": e.risk_score,
                   "aliases": (e.attributes or {}).get("aliases", [])} if e else None
    d["sibling_accounts"] = [row(x, ["id", "account_number", "bank", "risk_score"]) for x in
                             db.scalars(select(Account).where(Account.entity_id == a.entity_id, Account.id != a.id))] if e else []
    run = latest_run_id(db)
    d["alerts"] = [row(x, ALERT_FIELDS) for x in db.scalars(select(Alert).where(Alert.run_id == run, Alert.account_id == a.id).order_by(Alert.score.desc()))] if run else []
    # top counterparties
    for key, me, other in (("top_sources", Transaction.receiver_account_id, Transaction.sender_account_id),
                           ("top_destinations", Transaction.sender_account_id, Transaction.receiver_account_id)):
        rows = db.execute(select(other.label("acct"), func.count().label("n"), func.sum(Transaction.amount).label("total"),
                                 func.max(Transaction.risk_score).label("max_risk")).where(me == a.id).group_by(other)
                          .order_by(func.sum(Transaction.amount).desc()).limit(10)).all()
        accts = {x.id: x for x in db.scalars(select(Account).where(Account.id.in_([r.acct for r in rows])))} if rows else {}
        d[key] = [{"account_id": r.acct, "account_number": accts[r.acct].account_number, "holder": accts[r.acct].holder_name,
                   "count": r.n, "total": float(r.total), "max_risk": float(r.max_risk or 0),
                   "risk_score": accts[r.acct].risk_score} for r in rows]
    # monthly activity
    txns = db.execute(select(Transaction.timestamp, Transaction.amount, Transaction.sender_account_id, Transaction.is_flagged)
                      .where(or_(Transaction.sender_account_id == a.id, Transaction.receiver_account_id == a.id))).all()
    monthly: dict[str, dict] = {}
    for ts, amt, s, fl in txns:
        k = ts.strftime("%Y-%m")
        m = monthly.setdefault(k, {"month": k, "in": 0.0, "out": 0.0, "count": 0, "flagged": 0})
        m["out" if s == a.id else "in"] += float(amt)
        m["count"] += 1
        m["flagged"] += int(bool(fl))
    d["monthly"] = [monthly[k] for k in sorted(monthly)]
    d["cases"] = [{"id": c.id, "case_number": c.case_number, "title": c.title, "status": c.status} for c in db.scalars(
        select(Case).join(CaseAccount, CaseAccount.case_id == Case.id).where(CaseAccount.account_id == a.id))]
    return d


@router.get("/accounts/{account_id}/flow")
def account_flow(account_id: int, direction: str = "both", depth: int = Query(2, ge=1, le=4), min_amount: float = 0,
                 date_from: datetime | None = None, date_to: datetime | None = None, db: Session = Depends(get_db),
                 _: User = Depends(get_current_user)):
    get_or_404(db, Account, account_id, "Account")
    if direction not in ("both", "upstream", "downstream"):
        raise HTTPException(422, "direction must be both|upstream|downstream")
    return money_flow(db, account_id, direction, depth, min_amount, date_from, date_to)
