from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import user_out
from app.api.common import EMAIL_PATTERN, get_or_404
from app.core.db import get_db
from app.core.deps import client_ip, get_current_user, require
from app.core.security import hash_password, validate_password_strength
from app.models import Role, User
from app.services.audit import audit

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=255)
    full_name: str = Field(min_length=1, max_length=255)
    role: str
    password: str


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


@router.get("")
def list_users(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    # all authenticated users can list colleagues (needed to assign cases); only admins can modify
    return [user_out(u) for u in db.scalars(select(User).order_by(User.full_name))]


@router.post("", status_code=201)
def create_user(body: UserCreate, request: Request, db: Session = Depends(get_db), admin: User = Depends(require("users:manage"))):
    if body.role not in Role.ALL:
        raise HTTPException(422, f"Role must be one of {Role.ALL}")
    if err := validate_password_strength(body.password):
        raise HTTPException(422, err)
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "A user with this email already exists")
    u = User(email=body.email.lower(), full_name=body.full_name.strip(), role=body.role, hashed_password=hash_password(body.password))
    db.add(u)
    db.flush()
    audit(db, admin, "user.created", "user", u.id, {"email": u.email, "role": u.role}, client_ip(request))
    db.commit()
    return user_out(u)


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserUpdate, request: Request, db: Session = Depends(get_db), admin: User = Depends(require("users:manage"))):
    u = get_or_404(db, User, user_id, "User")
    changes = {}
    if body.role is not None:
        if body.role not in Role.ALL:
            raise HTTPException(422, f"Role must be one of {Role.ALL}")
        if u.id == admin.id and body.role != Role.ADMIN:
            raise HTTPException(400, "You cannot remove your own administrator role")
        changes["role"] = [u.role, body.role]
        u.role = body.role
    if body.is_active is not None:
        if u.id == admin.id and not body.is_active:
            raise HTTPException(400, "You cannot deactivate your own account")
        changes["is_active"] = [u.is_active, body.is_active]
        u.is_active = body.is_active
    if body.full_name is not None:
        u.full_name = body.full_name.strip()
        changes["full_name"] = True
    if body.password:
        if err := validate_password_strength(body.password):
            raise HTTPException(422, err)
        u.hashed_password = hash_password(body.password)
        changes["password"] = "reset"
    audit(db, admin, "user.updated", "user", u.id, changes, client_ip(request))
    db.commit()
    return user_out(u)
