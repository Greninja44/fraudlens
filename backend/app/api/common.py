from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.timeutil import as_utc


def jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return as_utc(v).isoformat()
    return v


def row(obj, fields: list[str]) -> dict:
    return {f: jsonable(getattr(obj, f)) for f in fields}


def get_or_404(db: Session, model, id_, what: str | None = None):
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(404, f"{what or model.__name__} not found")
    return obj


def page_params(page: int, page_size: int, max_size: int = 500) -> tuple[int, int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), max_size)
    return (page - 1) * page_size, page_size


USER_FIELDS = ["id", "email", "full_name", "role", "is_active", "created_at", "last_login_at"]
ACCOUNT_FIELDS = ["id", "account_number", "bank", "holder_name", "entity_id", "first_seen", "last_seen", "txn_count",
                  "total_in", "total_out", "risk_score", "risk_level", "alert_count", "patterns"]
TXN_FIELDS = ["id", "txn_id", "sender_account_id", "receiver_account_id", "sender_name", "receiver_name", "amount", "currency",
              "timestamp", "txn_type", "bank", "reference", "ip_address", "status", "import_batch_id", "data_warnings",
              "risk_score", "risk_level", "anomaly_score", "is_flagged", "patterns"]
ALERT_FIELDS = ["id", "run_id", "kind", "pattern", "severity", "confidence", "score", "title", "reasons", "details", "account_id",
                "transaction_ids", "first_txn_at", "last_txn_at", "total_amount", "review_status", "reviewed_by_id",
                "reviewed_at", "created_at"]


# Simple, permissive email format (allows internal domains such as *.local, which strict validators reject).
EMAIL_PATTERN = r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,63}$"
