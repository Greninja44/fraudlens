from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import EMAIL_PATTERN, USER_FIELDS, row
from app.core.db import get_db
from app.core.deps import PERMISSIONS, client_ip, get_current_user
from app.core.security import create_access_token, hash_password, validate_password_strength, verify_password
from app.models import User, utcnow
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])

# Constant-time-ish defence against user enumeration: verify against a dummy hash when user is unknown.
_DUMMY_HASH = hash_password("dummy-password-for-timing-1A")


class LoginIn(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=255)
    password: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


def user_out(u: User) -> dict:
    d = row(u, USER_FIELDS)
    d["permissions"] = sorted(p for p, roles in PERMISSIONS.items() if u.role in roles)
    return d


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    ok = verify_password(body.password, user.hashed_password if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        audit(db, None, "auth.login_failed", "user", None, {"email": body.email.lower()}, client_ip(request))
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    user.last_login_at = utcnow()
    audit(db, user, "auth.login", "user", user.id, ip=client_ip(request))
    db.commit()
    return {"access_token": create_access_token(user.id, user.role), "token_type": "bearer", "user": user_out(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_out(user)


@router.post("/change-password")
def change_password(body: PasswordChangeIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(400, "Current password is incorrect")
    if err := validate_password_strength(body.new_password):
        raise HTTPException(422, err)
    user.hashed_password = hash_password(body.new_password)
    audit(db, user, "auth.password_changed", "user", user.id, ip=client_ip(request))
    db.commit()
    return {"ok": True}
