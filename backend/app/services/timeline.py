"""Investigation timeline: merges transactions, detection alerts, evidence custody, notes and case events
into one chronological stream for a case or an account."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.timeutil import as_utc
from app.models import (Account, Alert, Case, CaseAccount, CaseEvent, CaseNote, CaseTransaction, CustodyEvent,
                        Evidence, Transaction)
from app.services.graph import latest_run_id

KINDS = ("transaction", "detection", "evidence", "note", "case")


def _in_range(ts, date_from, date_to):
    ts = as_utc(ts)
    return ts is not None and (date_from is None or ts >= as_utc(date_from)) and (date_to is None or ts <= as_utc(date_to))


def _txn_event(t: Transaction, accts: dict[int, Account]) -> dict:
    s, r = accts.get(t.sender_account_id), accts.get(t.receiver_account_id)
    return {"kind": "transaction", "id": f"txn-{t.id}", "timestamp": as_utc(t.timestamp).isoformat(),
            "title": f"{s.holder_name if s else '?'} → {r.holder_name if r else '?'}",
            "description": f"{t.txn_type or 'transfer'} of {float(t.amount):,.2f} {t.currency}" + (f" · ref {t.reference}" if t.reference else ""),
            "amount": float(t.amount), "risk_score": t.risk_score, "risk_level": t.risk_level, "flagged": t.is_flagged,
            "patterns": t.patterns or [], "account_ids": [t.sender_account_id, t.receiver_account_id],
            "ref": {"transaction_id": t.id, "txn_id": t.txn_id}}


def build_timeline(db: Session, *, case_id: int | None = None, account_id: int | None = None,
                   date_from: datetime | None = None, date_to: datetime | None = None, kinds: list[str] | None = None,
                   flagged_only: bool = False, limit: int = 2000) -> dict:
    kinds = [k for k in (kinds or KINDS) if k in KINDS]
    events: list[dict] = []
    account_ids: set[int] = set()
    txn_ids: set[int] = set()
    if case_id is not None:
        account_ids = set(db.scalars(select(CaseAccount.account_id).where(CaseAccount.case_id == case_id)))
        txn_ids = set(db.scalars(select(CaseTransaction.transaction_id).where(CaseTransaction.case_id == case_id)))
    if account_id is not None:
        account_ids.add(account_id)

    if "transaction" in kinds and (account_ids or txn_ids):
        conds = []
        if account_ids:
            conds.append(or_(Transaction.sender_account_id.in_(account_ids), Transaction.receiver_account_id.in_(account_ids)))
        if txn_ids:
            conds.append(Transaction.id.in_(txn_ids))
        q = select(Transaction).where(or_(*conds))
        if date_from:
            q = q.where(Transaction.timestamp >= date_from)
        if date_to:
            q = q.where(Transaction.timestamp <= date_to)
        if flagged_only:
            q = q.where(or_(Transaction.is_flagged.is_(True), Transaction.id.in_(txn_ids or {-1})))
        txns = db.scalars(q.order_by(Transaction.timestamp).limit(limit)).all()
        ids = {t.sender_account_id for t in txns} | {t.receiver_account_id for t in txns}
        accts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(ids)))} if ids else {}
        for t in txns:
            e = _txn_event(t, accts)
            e["in_case"] = t.id in txn_ids
            events.append(e)

    if "detection" in kinds and account_ids:
        run = latest_run_id(db)
        if run:
            alerts = db.scalars(select(Alert).where(Alert.run_id == run, Alert.kind == "rule", Alert.account_id.in_(account_ids))).all()
            for a in alerts:
                ts = a.last_txn_at or a.created_at
                if _in_range(ts, date_from, date_to):
                    events.append({"kind": "detection", "id": f"alert-{a.id}", "timestamp": as_utc(ts).isoformat(),
                                   "title": f"Detection: {a.title}", "description": "; ".join(a.reasons[:2]),
                                   "severity": a.severity, "confidence": a.confidence, "pattern": a.pattern,
                                   "account_ids": a.details.get("account_ids", []),
                                   "ref": {"alert_id": a.id, "transaction_ids": a.transaction_ids}})

    if case_id is not None:
        if "evidence" in kinds:
            rows = db.execute(select(CustodyEvent, Evidence).join(Evidence, Evidence.id == CustodyEvent.evidence_id)
                              .where(Evidence.case_id == case_id, CustodyEvent.action.in_(["uploaded", "verified", "verification_failed", "metadata_modified"]))).all()
            for ce, ev in rows:
                if _in_range(ce.created_at, date_from, date_to):
                    events.append({"kind": "evidence", "id": f"custody-{ce.id}", "timestamp": as_utc(ce.created_at).isoformat(),
                                   "title": f"Evidence {ev.evidence_code} {ce.action.replace('_', ' ')}",
                                   "description": f"{ev.original_filename} · by {ce.actor_email or 'system'}",
                                   "ref": {"evidence_id": ev.id}})
        if "note" in kinds:
            for n in db.scalars(select(CaseNote).where(CaseNote.case_id == case_id)):
                if _in_range(n.created_at, date_from, date_to):
                    events.append({"kind": "note", "id": f"note-{n.id}", "timestamp": as_utc(n.created_at).isoformat(),
                                   "title": f"Note by {n.author.full_name if n.author else 'unknown'}", "description": n.body[:500],
                                   "ref": {"note_id": n.id}})
        if "case" in kinds:
            for ce in db.scalars(select(CaseEvent).where(CaseEvent.case_id == case_id, CaseEvent.event_type != "evidence_added")):
                if _in_range(ce.created_at, date_from, date_to):
                    events.append({"kind": "case", "id": f"case-{ce.id}", "timestamp": as_utc(ce.created_at).isoformat(),
                                   "title": ce.event_type.replace("_", " ").capitalize(), "description": ce.description,
                                   "actor": ce.actor.full_name if ce.actor else None, "ref": {"case_event_id": ce.id, **(ce.payload or {})}})

    events.sort(key=lambda e: e["timestamp"])
    truncated = len(events) > limit
    return {"events": events[:limit], "truncated": truncated, "count": min(len(events), limit),
            "account_ids": sorted(account_ids), "kinds": kinds}
