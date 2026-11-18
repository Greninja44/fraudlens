from sqlalchemy.orm import Session

from app.models import AuditLog, User


def audit(db: Session, actor: User | None, action: str, resource_type: str | None = None,
          resource_id=None, details: dict | None = None, ip: str | None = None) -> None:
    """Add an audit record to the session (committed with the caller's transaction)."""
    db.add(AuditLog(actor_id=actor.id if actor else None, actor_email=actor.email if actor else None,
                    action=action, resource_type=resource_type,
                    resource_id=str(resource_id) if resource_id is not None else None,
                    details=details or {}, ip_address=ip))
