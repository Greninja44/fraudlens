from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import decode_token
from app.models import Role, User

bearer = HTTPBearer(auto_error=False)

# Permission matrix. Reviewers are read-mostly: they can review alerts, verify evidence and links,
# and move cases to/from review, but cannot import data, run detection or upload evidence.
PERMISSIONS: dict[str, set[str]] = {
    "data:import": {Role.ADMIN, Role.INVESTIGATOR},
    "detection:run": {Role.ADMIN, Role.INVESTIGATOR},
    "alerts:review": {Role.ADMIN, Role.INVESTIGATOR, Role.REVIEWER},
    "entities:verify": {Role.ADMIN, Role.INVESTIGATOR, Role.REVIEWER},
    "entities:merge": {Role.ADMIN, Role.INVESTIGATOR},
    "cases:write": {Role.ADMIN, Role.INVESTIGATOR},
    "cases:review": {Role.ADMIN, Role.REVIEWER},
    "evidence:upload": {Role.ADMIN, Role.INVESTIGATOR},
    "evidence:verify": {Role.ADMIN, Role.INVESTIGATOR, Role.REVIEWER},
    "evidence:edit": {Role.ADMIN, Role.INVESTIGATOR},
    "reports:generate": {Role.ADMIN, Role.INVESTIGATOR, Role.REVIEWER},
    "users:manage": {Role.ADMIN},
    "settings:manage": {Role.ADMIN},
    "audit:read": {Role.ADMIN, Role.REVIEWER},
}


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decode_token(creds.credentials)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User inactive or not found")
    return user


def has_permission(user: User, perm: str) -> bool:
    return user.role in PERMISSIONS[perm]


def require(perm: str) -> Callable[..., User]:
    def dep(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user, perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Your role ({user.role}) lacks permission '{perm}'")
        return user
    return dep


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
