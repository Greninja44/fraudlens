import io
import json
import re
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.common import ACCOUNT_FIELDS, TXN_FIELDS, get_or_404, jsonable, page_params, row
from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import client_ip, get_current_user, has_permission, require
from app.core.storage import get_storage
from app.models import (Account, Alert, Case, CaseAccount, CaseAssignee, CaseEvent, CaseFinding, CaseNote, CaseStatus,
                        CaseTransaction, CustodyEvent, Evidence, Report, Role, Transaction, User, utcnow)
from app.services import evidence as ev_svc
from app.services import reports as rep_svc
from app.services.audit import audit

router = APIRouter(tags=["cases"])
CASE_FIELDS = ["id", "case_number", "title", "summary", "objectives", "status", "priority", "created_by_id", "created_at",
               "updated_at", "closed_at"]
EVIDENCE_FIELDS = ["id", "evidence_code", "case_id", "original_filename", "content_type", "file_type", "category", "size_bytes",
                   "sha256", "description", "verification_status", "last_verified_at", "uploaded_by_id", "uploaded_at"]
PRIORITIES = ("low", "medium", "high", "critical")

# Allowed status transitions. Closing/reopening from review requires the cases:review permission.
TRANSITIONS = {
    CaseStatus.OPEN: {CaseStatus.UNDER_INVESTIGATION, CaseStatus.ESCALATED, CaseStatus.CLOSED},
    CaseStatus.UNDER_INVESTIGATION: {CaseStatus.PENDING_REVIEW, CaseStatus.ESCALATED, CaseStatus.OPEN},
    CaseStatus.PENDING_REVIEW: {CaseStatus.CLOSED, CaseStatus.UNDER_INVESTIGATION, CaseStatus.ESCALATED},
    CaseStatus.ESCALATED: {CaseStatus.UNDER_INVESTIGATION, CaseStatus.PENDING_REVIEW, CaseStatus.CLOSED},
    CaseStatus.CLOSED: {CaseStatus.UNDER_INVESTIGATION},
}
REVIEW_ONLY = {CaseStatus.CLOSED}


# ------------------------------------------------------------------ access helpers
def is_case_member(db: Session, user: User, case: Case) -> bool:
    if case.created_by_id == user.id:
        return True
    return db.scalar(select(CaseAssignee).where(CaseAssignee.case_id == case.id, CaseAssignee.user_id == user.id)) is not None


def ensure_case_read(db: Session, user: User, case: Case):
    """Admins and reviewers can read every case; investigators only cases they created or are assigned to."""
    if user.role in (Role.ADMIN, Role.REVIEWER) or is_case_member(db, user, case):
        return
    raise HTTPException(403, "You are not assigned to this case")


def ensure_case_write(db: Session, user: User, case: Case):
    if not has_permission(user, "cases:write"):
        raise HTTPException(403, f"Your role ({user.role}) cannot modify cases")
    if user.role != Role.ADMIN and not is_case_member(db, user, case):
        raise HTTPException(403, "Only the case creator, assignees or an administrator can modify this case")
    if case.status == CaseStatus.CLOSED:
        raise HTTPException(409, "Case is closed; reopen it before making changes")


def load_case(db: Session, case_id: int, user: User, write: bool = False) -> Case:
    c = get_or_404(db, Case, case_id, "Case")
    (ensure_case_write if write else ensure_case_read)(db, user, c)
    return c


def event(db, case: Case, etype: str, desc: str, user: User | None, payload: dict | None = None):
    db.add(CaseEvent(case_id=case.id, event_type=etype, description=desc, actor_id=user.id if user else None, payload=payload or {}))
    case.updated_at = utcnow()


def next_case_number(db: Session) -> str:
    year = utcnow().year
    n = (db.scalar(select(func.count()).select_from(Case)) or 0) + 1
    while db.scalar(select(Case.id).where(Case.case_number == f"FC-{year}-{n:04d}")):
        n += 1
    return f"FC-{year}-{n:04d}"


def case_out(db: Session, c: Case, full: bool = False) -> dict:
    d = row(c, CASE_FIELDS)
    d["created_by"] = c.created_by.full_name if c.created_by else None
    d["assignees"] = [{"id": u.id, "full_name": u.full_name, "role": u.role} for u in c.assignees]
    counts = {
        "accounts": db.scalar(select(func.count()).select_from(CaseAccount).where(CaseAccount.case_id == c.id)),
        "transactions": db.scalar(select(func.count()).select_from(CaseTransaction).where(CaseTransaction.case_id == c.id)),
        "evidence": db.scalar(select(func.count()).select_from(Evidence).where(Evidence.case_id == c.id)),
        "notes": db.scalar(select(func.count()).select_from(CaseNote).where(CaseNote.case_id == c.id)),
        "findings": db.scalar(select(func.count()).select_from(CaseFinding).where(CaseFinding.case_id == c.id)),
        "reports": db.scalar(select(func.count()).select_from(Report).where(Report.case_id == c.id)),
    }
    d["counts"] = counts
    # Progress checklist - derived from what has actually been recorded on the case
    steps = [("Subjects identified", counts["accounts"] > 0), ("Transactions linked", counts["transactions"] > 0),
             ("Evidence attached", counts["evidence"] > 0), ("Observations recorded", counts["notes"] > 0),
             ("Findings recorded", counts["findings"] > 0), ("Report generated", counts["reports"] > 0)]
    d["progress"] = {"steps": [{"label": l, "done": ok} for l, ok in steps], "percent": round(100 * sum(ok for _, ok in steps) / len(steps))}
    if full:
        accts = db.scalars(select(CaseAccount).where(CaseAccount.case_id == c.id).order_by(CaseAccount.added_at)).all()
        d["accounts"] = [{**row(ca.account, ACCOUNT_FIELDS), "case_role": ca.role, "note": ca.note, "added_at": jsonable(ca.added_at)} for ca in accts]
        cts = db.scalars(select(CaseTransaction).where(CaseTransaction.case_id == c.id)).all()
        txns = sorted([ct.transaction for ct in cts], key=lambda t: t.timestamp)
        ids = {t.sender_account_id for t in txns} | {t.receiver_account_id for t in txns}
        amap = {a.id: a.account_number for a in db.scalars(select(Account).where(Account.id.in_(ids)))} if ids else {}
        d["transactions"] = [{**row(t, TXN_FIELDS), "sender_account": amap.get(t.sender_account_id), "receiver_account": amap.get(t.receiver_account_id)} for t in txns]
        d["notes"] = [{"id": n.id, "body": n.body, "author": n.author.full_name if n.author else None, "created_at": jsonable(n.created_at)}
                      for n in db.scalars(select(CaseNote).where(CaseNote.case_id == c.id).order_by(CaseNote.created_at.desc()))]
        d["findings"] = [{"id": f.id, "title": f.title, "description": f.description, "status": f.status, "related_alert_ids": f.related_alert_ids,
                          "author": f.author.full_name if f.author else None, "created_at": jsonable(f.created_at)}
                         for f in db.scalars(select(CaseFinding).where(CaseFinding.case_id == c.id).order_by(CaseFinding.created_at))]
        d["events"] = [{"id": e.id, "event_type": e.event_type, "description": e.description, "actor": e.actor.full_name if e.actor else None,
                        "created_at": jsonable(e.created_at)} for e in db.scalars(select(CaseEvent).where(CaseEvent.case_id == c.id).order_by(CaseEvent.created_at.desc()))]
        d["evidence"] = [evidence_out(e) for e in db.scalars(select(Evidence).where(Evidence.case_id == c.id).order_by(Evidence.uploaded_at.desc()))]
        d["reports"] = [report_out(r) for r in db.scalars(select(Report).where(Report.case_id == c.id).order_by(Report.generated_at.desc()))]
        d["allowed_transitions"] = sorted(TRANSITIONS.get(c.status, set()))
    return d


def evidence_out(e: Evidence) -> dict:
    d = row(e, EVIDENCE_FIELDS)
    d["uploaded_by"] = e.uploaded_by.full_name if e.uploaded_by else None
    d["case_number"] = e.case.case_number if e.case else None
    return d


def report_out(r: Report) -> dict:
    return {**row(r, ["id", "case_id", "format", "filename", "sha256", "size_bytes", "generated_at"]),
            "generated_by": r.generated_by.full_name if r.generated_by else None}


# ------------------------------------------------------------------ cases
class CaseCreate(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    summary: str = Field(default="", max_length=20000)
    objectives: str = Field(default="", max_length=20000)
    priority: str = "medium"
    assignee_ids: list[int] = []
    account_ids: list[int] = []
    transaction_ids: list[int] = []
    alert_ids: list[int] = []


class CaseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=255)
    summary: str | None = Field(default=None, max_length=20000)
    objectives: str | None = Field(default=None, max_length=20000)
    priority: str | None = None


def _add_accounts(db, c, ids, role, note, user):
    existing = set(db.scalars(select(CaseAccount.account_id).where(CaseAccount.case_id == c.id)))
    added = []
    for aid in dict.fromkeys(ids):
        if aid in existing:
            continue
        a = get_or_404(db, Account, aid, f"Account {aid}")
        db.add(CaseAccount(case_id=c.id, account_id=aid, role=role, note=note, added_by_id=user.id))
        added.append(a.account_number)
    if added:
        event(db, c, "accounts_added", f"Added {len(added)} account(s) as {role}: {', '.join(added[:10])}", user, {"accounts": added})
    return added


def _add_transactions(db, c, ids, note, user):
    existing = set(db.scalars(select(CaseTransaction.transaction_id).where(CaseTransaction.case_id == c.id)))
    new = [i for i in dict.fromkeys(ids) if i not in existing]
    found = set(db.scalars(select(Transaction.id).where(Transaction.id.in_(new)))) if new else set()
    missing = set(new) - found
    if missing:
        raise HTTPException(404, f"Transactions not found: {sorted(missing)[:10]}")
    for tid in new:
        db.add(CaseTransaction(case_id=c.id, transaction_id=tid, note=note, added_by_id=user.id))
    if new:
        event(db, c, "transactions_added", f"Associated {len(new)} transaction(s) with the case", user, {"transaction_ids": new[:200]})
    return new


MAX_ALERT_ACCOUNTS = 25


def _alert_material(db, alert_ids):
    """Primary accounts (subjects), other involved accounts (related) and transactions of the given alerts."""
    primary, related, txns = [], [], []
    for aid in alert_ids:
        a = get_or_404(db, Alert, aid, f"Alert {aid}")
        if a.account_id:
            primary.append(a.account_id)
        related += [x for x in (a.details or {}).get("account_ids", []) if x != a.account_id][:MAX_ALERT_ACCOUNTS]
        txns += a.transaction_ids or []
    related = [x for x in dict.fromkeys(related) if x not in primary]
    return primary, related, txns


@router.post("/cases", status_code=201)
def create_case(body: CaseCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases:write"))):
    if body.priority not in PRIORITIES:
        raise HTTPException(422, f"priority must be one of {PRIORITIES}")
    c = Case(case_number=next_case_number(db), title=body.title.strip(), summary=body.summary, objectives=body.objectives,
             priority=body.priority, created_by_id=user.id)
    db.add(c)
    db.flush()
    assignees = set(body.assignee_ids) or {user.id}
    for uid in assignees:
        u = get_or_404(db, User, uid, f"User {uid}")
        if u.role == Role.REVIEWER:
            raise HTTPException(422, f"{u.full_name} is a reviewer and cannot be assigned as investigator")
        db.add(CaseAssignee(case_id=c.id, user_id=uid))
    event(db, c, "case_created", f"Case {c.case_number} opened: {c.title}", user)
    a_accts, a_related, a_txns = _alert_material(db, body.alert_ids)
    _add_accounts(db, c, [*body.account_ids, *a_accts], "subject", None, user)
    _add_accounts(db, c, a_related, "related", "Involved in the linked alert", user)
    _add_transactions(db, c, [*body.transaction_ids, *a_txns], None, user)
    if body.alert_ids:
        event(db, c, "alerts_linked", f"Created from alert(s) {', '.join(map(str, body.alert_ids))}", user, {"alert_ids": body.alert_ids})
    audit(db, user, "case.created", "case", c.id, {"case_number": c.case_number}, client_ip(request))
    db.commit()
    db.refresh(c)
    return case_out(db, c, full=True)


@router.get("/cases")
def list_cases(status: str | None = None, priority: str | None = None, search: str | None = None, mine: bool = False,
               page: int = 1, page_size: int = 50, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    off, lim = page_params(page, page_size)
    q = select(Case)
    if user.role == Role.INVESTIGATOR or mine:
        member_ids = select(CaseAssignee.case_id).where(CaseAssignee.user_id == user.id)
        q = q.where(or_(Case.created_by_id == user.id, Case.id.in_(member_ids)))
    if status:
        q = q.where(Case.status.in_(status.split(",")))
    if priority:
        q = q.where(Case.priority == priority)
    if search:
        like = f"%{search.strip()}%"
        q = q.where(or_(Case.title.ilike(like), Case.case_number.ilike(like), Case.summary.ilike(like)))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(Case.updated_at.desc()).offset(off).limit(lim)).all()
    return {"total": total, "items": [case_out(db, c) for c in items]}


@router.get("/cases/{case_id}")
def get_case(case_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return case_out(db, load_case(db, case_id, user), full=True)


@router.patch("/cases/{case_id}")
def update_case(case_id: int, body: CaseUpdate, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = load_case(db, case_id, user, write=True)
    changed = []
    for f in ("title", "summary", "objectives", "priority"):
        v = getattr(body, f)
        if v is not None and v != getattr(c, f):
            if f == "priority" and v not in PRIORITIES:
                raise HTTPException(422, f"priority must be one of {PRIORITIES}")
            setattr(c, f, v)
            changed.append(f)
    if changed:
        event(db, c, "case_updated", f"Updated {', '.join(changed)}", user)
        audit(db, user, "case.updated", "case", c.id, {"fields": changed}, client_ip(request))
    db.commit()
    return case_out(db, c, full=True)


class StatusIn(BaseModel):
    status: str
    comment: str | None = Field(default=None, max_length=5000)


@router.post("/cases/{case_id}/status")
def change_status(case_id: int, body: StatusIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = load_case(db, case_id, user)
    if body.status not in CaseStatus.ALL:
        raise HTTPException(422, f"status must be one of {CaseStatus.ALL}")
    if body.status not in TRANSITIONS.get(c.status, set()):
        raise HTTPException(409, f"Cannot move a case from '{c.status}' to '{body.status}'")
    reviewing = c.status in (CaseStatus.PENDING_REVIEW, CaseStatus.CLOSED) or body.status in REVIEW_ONLY
    if reviewing and not has_permission(user, "cases:review"):
        raise HTTPException(403, "Closing, reopening or deciding on a case under review requires a reviewer or administrator")
    if not reviewing:
        if not has_permission(user, "cases:write") or (user.role != Role.ADMIN and not is_case_member(db, user, c)):
            raise HTTPException(403, "Only case members or an administrator can change this case's status")
    old = c.status
    c.status = body.status
    c.closed_at = utcnow() if body.status == CaseStatus.CLOSED else None
    event(db, c, "status_changed", f"Status changed from {old.replace('_', ' ')} to {body.status.replace('_', ' ')}" +
          (f" - {body.comment}" if body.comment else ""), user, {"from": old, "to": body.status})
    audit(db, user, "case.status_changed", "case", c.id, {"from": old, "to": body.status}, client_ip(request))
    db.commit()
    return case_out(db, c, full=True)


class AssigneesIn(BaseModel):
    user_ids: list[int]


@router.put("/cases/{case_id}/assignees")
def set_assignees(case_id: int, body: AssigneesIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = load_case(db, case_id, user, write=True)
    if not body.user_ids:
        raise HTTPException(422, "A case needs at least one assignee")
    users = []
    for uid in dict.fromkeys(body.user_ids):
        u = get_or_404(db, User, uid, f"User {uid}")
        if u.role == Role.REVIEWER or not u.is_active:
            raise HTTPException(422, f"{u.full_name} cannot be assigned (reviewer or inactive)")
        users.append(u)
    db.query(CaseAssignee).filter(CaseAssignee.case_id == c.id).delete()
    for u in users:
        db.add(CaseAssignee(case_id=c.id, user_id=u.id))
    event(db, c, "assignees_changed", f"Assigned to {', '.join(u.full_name for u in users)}", user, {"user_ids": [u.id for u in users]})
    audit(db, user, "case.assigned", "case", c.id, {"user_ids": [u.id for u in users]}, client_ip(request))
    db.commit()
    db.refresh(c)
    return case_out(db, c, full=True)


class AccountsIn(BaseModel):
    account_ids: list[int]
    role: str = "subject"
    note: str | None = None


@router.post("/cases/{case_id}/accounts")
def add_accounts(case_id: int, body: AccountsIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = load_case(db, case_id, user, write=True)
    if body.role not in ("subject", "counterparty", "related"):
        raise HTTPException(422, "role must be subject|counterparty|related")
    added = _add_accounts(db, c, body.account_ids, body.role, body.note, user)
    audit(db, user, "case.accounts_added", "case", c.id, {"accounts": added}, client_ip(request))
    db.commit()
    return case_out(db, c, full=True)


@router.delete("/cases/{case_id}/accounts/{account_id}")
def remove_account(case_id: int, account_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = load_case(db, case_id, user, write=True)
    ca = db.get(CaseAccount, (case_id, account_id))
    if not ca:
        raise HTTPException(404, "Account not in case")
    num = ca.account.account_number
    db.delete(ca)
    event(db, c, "account_removed", f"Removed account {num}", user)
    audit(db, user, "case.account_removed", "case", c.id, {"account": num}, client_ip(request))
    db.commit()
    return case_out(db, c, full=True)


class TxnsIn(BaseModel):
    transaction_ids: list[int] = []
    alert_id: int | None = None
    note: str | None = None
    include_accounts: bool = False


@router.post("/cases/{case_id}/transactions")
def add_transactions(case_id: int, body: TxnsIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = load_case(db, case_id, user, write=True)
    ids = list(body.transaction_ids)
    if body.alert_id:
        accts, related, txns = _alert_material(db, [body.alert_id])
        ids += txns
        if body.include_accounts:
            _add_accounts(db, c, accts, "subject", None, user)
            _add_accounts(db, c, related, "related", "Involved in the linked alert", user)
        event(db, c, "alerts_linked", f"Linked alert {body.alert_id}", user, {"alert_ids": [body.alert_id]})
    if not ids:
        raise HTTPException(422, "No transactions given")
    new = _add_transactions(db, c, ids, body.note, user)
    audit(db, user, "case.transactions_added", "case", c.id, {"count": len(new)}, client_ip(request))
    db.commit()
    return case_out(db, c, full=True)


@router.delete("/cases/{case_id}/transactions/{txn_pk}")
def remove_transaction(case_id: int, txn_pk: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = load_case(db, case_id, user, write=True)
    ct = db.get(CaseTransaction, (case_id, txn_pk))
    if not ct:
        raise HTTPException(404, "Transaction not in case")
    tid = ct.transaction.txn_id
    db.delete(ct)
    event(db, c, "transaction_removed", f"Removed transaction {tid}", user)
    audit(db, user, "case.transaction_removed", "case", c.id, {"txn_id": tid}, client_ip(request))
    db.commit()
    return case_out(db, c, full=True)


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


@router.post("/cases/{case_id}/notes", status_code=201)
def add_note(case_id: int, body: NoteIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = load_case(db, case_id, user)
    if c.status == CaseStatus.CLOSED:
        raise HTTPException(409, "Case is closed")
    if not (has_permission(user, "cases:write") and (user.role == Role.ADMIN or is_case_member(db, user, c))) and user.role != Role.REVIEWER:
        raise HTTPException(403, "Not allowed to add notes to this case")
    n = CaseNote(case_id=c.id, author_id=user.id, body=body.body.strip())
    db.add(n)
    c.updated_at = utcnow()
    audit(db, user, "case.note_added", "case", c.id, ip=client_ip(request))
    db.commit()
    return {"id": n.id, "body": n.body, "author": user.full_name, "created_at": jsonable(n.created_at)}


class FindingIn(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    description: str = Field(min_length=1, max_length=20000)
    status: str = "draft"
    related_alert_ids: list[int] = []


@router.post("/cases/{case_id}/findings", status_code=201)
def add_finding(case_id: int, body: FindingIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = load_case(db, case_id, user, write=True)
    if body.status not in ("draft", "confirmed", "refuted"):
        raise HTTPException(422, "status must be draft|confirmed|refuted")
    f = CaseFinding(case_id=c.id, author_id=user.id, title=body.title, description=body.description, status=body.status,
                    related_alert_ids=body.related_alert_ids)
    db.add(f)
    db.flush()
    event(db, c, "finding_recorded", f"Investigator finding recorded ({body.status}): {body.title}", user, {"finding_id": f.id})
    audit(db, user, "case.finding_added", "case", c.id, {"finding_id": f.id, "status": body.status}, client_ip(request))
    db.commit()
    return case_out(db, c, full=True)


class FindingStatusIn(BaseModel):
    status: str


@router.patch("/cases/{case_id}/findings/{finding_id}")
def update_finding(case_id: int, finding_id: int, body: FindingStatusIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = load_case(db, case_id, user, write=True)
    f = get_or_404(db, CaseFinding, finding_id, "Finding")
    if f.case_id != c.id:
        raise HTTPException(404, "Finding not found")
    if body.status not in ("draft", "confirmed", "refuted"):
        raise HTTPException(422, "status must be draft|confirmed|refuted")
    old, f.status = f.status, body.status
    event(db, c, "finding_updated", f"Finding '{f.title}' changed from {old} to {body.status}", user)
    audit(db, user, "case.finding_updated", "case", c.id, {"finding_id": f.id, "from": old, "to": body.status}, client_ip(request))
    db.commit()
    return case_out(db, c, full=True)


# ------------------------------------------------------------------ evidence
@router.post("/cases/{case_id}/evidence", status_code=201)
async def upload_evidence(case_id: int, request: Request, file: UploadFile = File(...), description: str = Form(""),
                          category: str = Form("supporting_document"), db: Session = Depends(get_db),
                          user: User = Depends(require("evidence:upload"))):
    c = load_case(db, case_id, user, write=True)
    try:
        e = ev_svc.add_evidence(db, c.id, file.file, file.filename or "", description[:5000], category, user,
                                get_settings().max_evidence_mb * 1024 * 1024, client_ip(request))
    except ev_svc.EvidenceError as err:
        db.rollback()
        raise HTTPException(422, str(err))
    audit(db, user, "evidence.uploaded", "evidence", e.id, {"code": e.evidence_code, "sha256": e.sha256}, client_ip(request))
    db.commit()
    return evidence_out(e)


@router.post("/cases/{case_id}/evidence/transaction-extract", status_code=201)
def evidence_from_transactions(case_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("evidence:upload"))):
    """Preserve the case's transaction records as an evidence item (system-generated CSV extract)."""
    c = load_case(db, case_id, user, write=True)
    data = rep_svc.collect_case_data(db, c)
    if not data["transactions"]:
        raise HTTPException(422, "The case has no transactions to extract")
    blob = rep_svc.transactions_csv(data)
    ts = utcnow().strftime("%Y%m%dT%H%M%SZ")
    e = ev_svc.add_evidence(db, c.id, io.BytesIO(blob), f"{c.case_number}_transaction_records_{ts}.csv",
                            f"System-generated extract of {len(data['transactions'])} case transaction records from the FraudLens database at {ts}.",
                            "transaction_records", user, get_settings().max_evidence_mb * 1024 * 1024, client_ip(request))
    audit(db, user, "evidence.uploaded", "evidence", e.id, {"code": e.evidence_code, "generated": True}, client_ip(request))
    db.commit()
    return evidence_out(e)


@router.get("/evidence")
def list_evidence(case_id: int | None = None, search: str | None = None, verification_status: str | None = None, file_type: str | None = None,
                  page: int = 1, page_size: int = 50, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    off, lim = page_params(page, page_size)
    q = select(Evidence)
    if user.role == Role.INVESTIGATOR:
        member_ids = select(CaseAssignee.case_id).where(CaseAssignee.user_id == user.id)
        q = q.join(Case, Case.id == Evidence.case_id).where(or_(Case.created_by_id == user.id, Case.id.in_(member_ids)))
    if case_id:
        q = q.where(Evidence.case_id == case_id)
    if search:
        like = f"%{search.strip()}%"
        q = q.where(or_(Evidence.original_filename.ilike(like), Evidence.evidence_code.ilike(like), Evidence.description.ilike(like),
                        Evidence.sha256.ilike(like)))
    if verification_status:
        q = q.where(Evidence.verification_status == verification_status)
    if file_type:
        q = q.where(Evidence.file_type == file_type)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(Evidence.uploaded_at.desc()).offset(off).limit(lim)).all()
    return {"total": total, "items": [evidence_out(e) for e in items]}


def _load_evidence(db, evidence_id, user) -> Evidence:
    e = get_or_404(db, Evidence, evidence_id, "Evidence")
    ensure_case_read(db, user, e.case)
    return e


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    e = _load_evidence(db, evidence_id, user)
    ev_svc.record_custody(db, e, "accessed", user, {"view": "metadata"}, client_ip(request))
    db.commit()
    d = evidence_out(e)
    d["custody"] = [row(x, ["id", "action", "actor_email", "details", "ip_address", "created_at", "prev_hash", "event_hash"])
                    for x in db.scalars(select(CustodyEvent).where(CustodyEvent.evidence_id == e.id).order_by(CustodyEvent.id))]
    d["chain"] = ev_svc.verify_chain(db, e)
    return d


@router.get("/evidence/{evidence_id}/download")
def download_evidence(evidence_id: int, request: Request, inline: bool = False, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    e = _load_evidence(db, evidence_id, user)
    storage = get_storage()
    if not storage.exists(e.storage_key):
        raise HTTPException(410, "Stored evidence file is missing - integrity failure")
    ev_svc.record_custody(db, e, "exported" if not inline else "accessed", user, {"download": not inline}, client_ip(request))
    audit(db, user, "evidence.downloaded", "evidence", e.id, {"code": e.evidence_code, "inline": inline}, client_ip(request))
    db.commit()
    fh = storage.open(e.storage_key)
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', e.original_filename)
    return StreamingResponse(iter(lambda: fh.read(65536), b""), media_type=e.content_type,
                             headers={"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{safe}"',
                                      "X-Evidence-SHA256": e.sha256, "X-Content-Type-Options": "nosniff",
                                      "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"})


@router.post("/evidence/{evidence_id}/verify")
def verify_evidence(evidence_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("evidence:verify"))):
    e = _load_evidence(db, evidence_id, user)
    res = ev_svc.verify_integrity(db, e, user, client_ip(request))
    audit(db, user, "evidence.verified", "evidence", e.id, {"match": res["hash_match"]}, client_ip(request))
    db.commit()
    return res


class EvidenceMetaIn(BaseModel):
    description: str | None = Field(default=None, max_length=5000)
    category: str | None = None


@router.patch("/evidence/{evidence_id}")
def update_evidence_meta(evidence_id: int, body: EvidenceMetaIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("evidence:edit"))):
    e = _load_evidence(db, evidence_id, user)
    ensure_case_write(db, user, e.case)
    changes = {}
    if body.description is not None and body.description != e.description:
        changes["description"] = {"from": e.description, "to": body.description}
        e.description = body.description
    if body.category is not None and body.category != e.category:
        if body.category not in ev_svc.CATEGORIES:
            raise HTTPException(422, f"category must be one of {ev_svc.CATEGORIES}")
        changes["category"] = {"from": e.category, "to": body.category}
        e.category = body.category
    if changes:
        ev_svc.record_custody(db, e, "metadata_modified", user, changes, client_ip(request))
        audit(db, user, "evidence.metadata_modified", "evidence", e.id, changes, client_ip(request))
        db.commit()
    return evidence_out(e)


@router.get("/evidence-categories")
def categories(_: User = Depends(get_current_user)):
    return {"categories": ev_svc.CATEGORIES, "extensions": sorted(ev_svc.ALLOWED), "max_mb": get_settings().max_evidence_mb}


# ------------------------------------------------------------------ reports & exports
@router.post("/cases/{case_id}/reports", status_code=201)
def generate_report(case_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("reports:generate"))):
    c = load_case(db, case_id, user)
    data = rep_svc.collect_case_data(db, c)
    pdf = rep_svc.build_pdf(data, user.full_name)
    ts = utcnow().strftime("%Y%m%dT%H%M%SZ")
    fname = f"{c.case_number}_report_{ts}.pdf"
    key = f"reports/case-{c.id}/{ts}-{fname}"
    sha, size = get_storage().put(key, io.BytesIO(pdf))
    r = Report(case_id=c.id, format="pdf", storage_key=key, filename=fname, sha256=sha, size_bytes=size, generated_by_id=user.id)
    db.add(r)
    event(db, c, "report_generated", f"PDF report generated ({fname})", user, {"sha256": sha})
    audit(db, user, "report.generated", "case", c.id, {"filename": fname, "sha256": sha}, client_ip(request))
    db.commit()
    return report_out(r)


@router.get("/reports")
def list_reports(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = select(Report).join(Case, Case.id == Report.case_id)
    if user.role == Role.INVESTIGATOR:
        member_ids = select(CaseAssignee.case_id).where(CaseAssignee.user_id == user.id)
        q = q.where(or_(Case.created_by_id == user.id, Case.id.in_(member_ids)))
    rows = db.scalars(q.order_by(Report.generated_at.desc()).limit(200)).all()
    cases = {c.id: c for c in db.scalars(select(Case).where(Case.id.in_({r.case_id for r in rows})))} if rows else {}
    return [{**report_out(r), "case_number": cases[r.case_id].case_number, "case_title": cases[r.case_id].title} for r in rows]


@router.get("/reports/{report_id}/download")
def download_report(report_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = get_or_404(db, Report, report_id, "Report")
    load_case(db, r.case_id, user)
    audit(db, user, "report.downloaded", "report", r.id, ip=client_ip(request))
    db.commit()
    fh = get_storage().open(r.storage_key)
    return StreamingResponse(iter(lambda: fh.read(65536), b""), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{r.filename}"', "X-Report-SHA256": r.sha256})


@router.get("/cases/{case_id}/export")
def export_case(case_id: int, request: Request, format: str = Query("json", pattern="^(json|csv)$"), db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    c = load_case(db, case_id, user)
    data = rep_svc.collect_case_data(db, c)
    audit(db, user, "case.exported", "case", c.id, {"format": format}, client_ip(request))
    db.commit()
    if format == "csv":
        return Response(rep_svc.transactions_csv(data), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{c.case_number}_transactions.csv"'})
    return Response(json.dumps(data, indent=1, default=str), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{c.case_number}_investigation.json"'})
