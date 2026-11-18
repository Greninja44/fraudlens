from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import page_params, row
from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import client_ip, get_current_user, require
from app.models import AuditLog, User
from app.services.audit import audit
from app.services.settings_service import DEFAULT_DETECTION, get_setting, update_setting

router = APIRouter(tags=["admin"])

SETTING_HELP = {
    "reporting_threshold": "Currency-transaction reporting threshold (USD) that structuring tries to avoid",
    "structuring_band": "Fraction below the threshold that counts as 'near-threshold' (0.15 = within 15%)",
    "structuring_min_count": "Minimum near-threshold transactions in the window",
    "structuring_window_days": "Window for structuring/smurfing clusters (days)",
    "smurfing_min_senders": "Minimum distinct senders to one receiver for smurfing",
    "rapid_window_hours": "Max hours between receiving and forwarding funds",
    "rapid_min_ratio": "Minimum share of an inbound amount forwarded",
    "rapid_min_amount": "Minimum inbound amount considered for pass-through / cycles (USD)",
    "velocity_window_minutes": "Rolling window for high-velocity detection (minutes)",
    "velocity_min_count": "Transactions within the window that trigger a velocity alert",
    "cycle_max_length": "Maximum hops in a circular flow",
    "cycle_max_span_days": "Maximum duration of a circular flow (days)",
    "cycle_amount_tolerance": "Allowed amount deviation per hop relative to the first hop",
    "dormant_days": "Inactivity (days) after which an account is considered dormant",
    "dormant_min_amount": "Minimum reactivation amount (USD)",
    "unusual_zscore": "Robust z-score above which an amount is unusual for the account",
    "unusual_min_history": "Minimum transactions needed to model an account's behaviour",
    "network_min_shared_counterparties": "Minimum shared uncommon counterparties for a network alert",
    "network_min_jaccard": "Minimum counterparty-set similarity (Jaccard) for a network alert",
    "ml_contamination": "Expected share of outliers for the Isolation Forest",
    "ml_weight": "Maximum contribution of the anomaly model to the 0-100 risk score",
    "flag_threshold": "Risk score at or above which a transaction is flagged for review",
}


@router.get("/settings/detection")
def get_detection_settings(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return {"values": get_setting(db, "detection"), "defaults": DEFAULT_DETECTION, "help": SETTING_HELP}


@router.put("/settings/detection")
def put_detection_settings(body: dict, request: Request, db: Session = Depends(get_db), admin: User = Depends(require("settings:manage"))):
    try:
        values = update_setting(db, "detection", body, admin.id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    audit(db, admin, "settings.updated", "settings", "detection", body, client_ip(request))
    db.commit()
    return {"values": values}


@router.get("/settings/system")
def system_info(_: User = Depends(get_current_user)):
    s = get_settings()
    return {"environment": s.env, "storage_backend": s.storage_backend, "max_import_mb": s.max_import_mb,
            "max_evidence_mb": s.max_evidence_mb, "access_token_minutes": s.access_token_minutes,
            "insecure_secret": s.secret_key.startswith("dev-only")}


@router.get("/audit")
def audit_log(page: int = 1, page_size: int = 50, action: str | None = None, actor_id: int | None = None,
              date_from: datetime | None = None, db: Session = Depends(get_db), _: User = Depends(require("audit:read"))):
    off, lim = page_params(page, page_size, 200)
    q = select(AuditLog)
    if action:
        q = q.where(AuditLog.action.startswith(action))
    if actor_id:
        q = q.where(AuditLog.actor_id == actor_id)
    if date_from:
        q = q.where(AuditLog.created_at >= date_from)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(AuditLog.id.desc()).offset(off).limit(lim)).all()
    return {"total": total, "items": [row(a, ["id", "actor_id", "actor_email", "action", "resource_type", "resource_id",
                                                "details", "ip_address", "created_at"]) for a in items]}
