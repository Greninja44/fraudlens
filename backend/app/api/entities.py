from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.common import ACCOUNT_FIELDS, get_or_404, page_params, row
from app.core.db import get_db
from app.core.deps import client_ip, get_current_user, require
from app.models import Account, Entity, EntityLink, User, utcnow
from app.services import entities as svc
from app.services.audit import audit

router = APIRouter(prefix="/entities", tags=["entities"])
ENT_FIELDS = ["id", "entity_type", "name", "normalized_name", "attributes", "risk_score", "merged_into_id", "created_at"]
LINK_FIELDS = ["id", "entity_a_id", "entity_b_id", "link_type", "confidence", "signals", "status", "verified_by_id", "verified_at", "note", "created_at"]


def link_out(l: EntityLink) -> dict:
    d = row(l, LINK_FIELDS)
    d["entity_a"] = {"id": l.entity_a.id, "name": l.entity_a.name, "entity_type": l.entity_a.entity_type, "risk_score": l.entity_a.risk_score}
    d["entity_b"] = {"id": l.entity_b.id, "name": l.entity_b.name, "entity_type": l.entity_b.entity_type, "risk_score": l.entity_b.risk_score}
    return d


@router.get("")
def list_entities(search: str | None = None, entity_type: str | None = None, min_risk: float | None = None, page: int = 1,
                  page_size: int = 50, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    off, lim = page_params(page, page_size)
    q = select(Entity).where(Entity.merged_into_id.is_(None))
    if search:
        q = q.where(Entity.name.ilike(f"%{search.strip()}%"))
    if entity_type:
        q = q.where(Entity.entity_type == entity_type)
    if min_risk is not None:
        q = q.where(Entity.risk_score >= min_risk)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(Entity.risk_score.desc(), Entity.name).offset(off).limit(lim)).all()
    counts = dict(db.execute(select(Account.entity_id, func.count()).where(Account.entity_id.in_([e.id for e in items])).group_by(Account.entity_id)).all()) if items else {}
    lcounts = {}
    if items:
        ids = [e.id for e in items]
        for a, b in db.execute(select(EntityLink.entity_a_id, EntityLink.entity_b_id).where(
                or_(EntityLink.entity_a_id.in_(ids), EntityLink.entity_b_id.in_(ids)), EntityLink.status != "rejected")):
            lcounts[a] = lcounts.get(a, 0) + 1
            lcounts[b] = lcounts.get(b, 0) + 1
    return {"total": total, "items": [{**row(e, ENT_FIELDS), "account_count": counts.get(e.id, 0), "link_count": lcounts.get(e.id, 0)} for e in items]}


@router.get("/links")
def list_links(status: str | None = None, link_type: str | None = None, min_confidence: float = 0, entity_id: int | None = None,
               page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    off, lim = page_params(page, page_size)
    q = select(EntityLink).where(EntityLink.confidence >= min_confidence)
    if status:
        q = q.where(EntityLink.status == status)
    if link_type:
        q = q.where(EntityLink.link_type == link_type)
    if entity_id:
        q = q.where(or_(EntityLink.entity_a_id == entity_id, EntityLink.entity_b_id == entity_id))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(EntityLink.confidence.desc(), EntityLink.id).offset(off).limit(lim)).all()
    return {"total": total, "items": [link_out(l) for l in items]}


@router.post("/links/recompute")
def recompute(request: Request, db: Session = Depends(get_db), user: User = Depends(require("entities:merge"))):
    res = svc.compute_links(db)
    audit(db, user, "entities.links_recomputed", "entity_link", None, res, client_ip(request))
    db.commit()
    return res


class LinkDecision(BaseModel):
    status: str  # verified|rejected|suggested
    note: str | None = None


@router.post("/links/{link_id}/decision")
def decide(link_id: int, body: LinkDecision, request: Request, db: Session = Depends(get_db), user: User = Depends(require("entities:verify"))):
    if body.status not in ("verified", "rejected", "suggested"):
        raise HTTPException(422, "status must be verified|rejected|suggested")
    l = get_or_404(db, EntityLink, link_id, "Link")
    old = l.status
    l.status, l.note = body.status, body.note
    l.verified_by_id, l.verified_at = (user.id, utcnow()) if body.status != "suggested" else (None, None)
    audit(db, user, "entities.link_decision", "entity_link", l.id, {"from": old, "to": body.status, "note": body.note}, client_ip(request))
    db.commit()
    return link_out(l)


class MergeIn(BaseModel):
    keep_entity_id: int


@router.post("/links/{link_id}/merge")
def merge(link_id: int, body: MergeIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("entities:merge"))):
    l = get_or_404(db, EntityLink, link_id, "Link")
    try:
        kept = svc.merge_entities(db, l, body.keep_entity_id, user.id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    audit(db, user, "entities.merged", "entity", kept.id, {"link_id": l.id, "kept": kept.id}, client_ip(request))
    db.commit()
    return row(kept, ENT_FIELDS)


@router.get("/{entity_id}")
def get_entity(entity_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    e = get_or_404(db, Entity, entity_id, "Entity")
    d = row(e, ENT_FIELDS)
    d["accounts"] = [row(a, ACCOUNT_FIELDS) for a in db.scalars(select(Account).where(Account.entity_id == e.id))]
    d["links"] = [link_out(l) for l in db.scalars(select(EntityLink).where(or_(EntityLink.entity_a_id == e.id, EntityLink.entity_b_id == e.id)).order_by(EntityLink.confidence.desc()))]
    return d
