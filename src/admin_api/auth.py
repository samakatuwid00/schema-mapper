"""Per-admin session authentication and role checks.

The acting identity always comes from the signed session cookie; client
bodies never carry an actor field (audit-and-approval spec).
"""
from __future__ import annotations

from dataclasses import dataclass

import bcrypt
from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, TimestampSigner

from . import db
from .settings import SESSION_COOKIE, SESSION_MAX_AGE_SECONDS, require_session_secret

_signer: TimestampSigner | None = None


def _get_signer() -> TimestampSigner:
    global _signer
    if _signer is None:
        _signer = TimestampSigner(require_session_secret())
    return _signer


@dataclass
class AdminUser:
    id: int
    username: str
    role: str


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def load_user(username: str) -> AdminUser | None:
    with db.central().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, username, role FROM integration.admin_user
                WHERE username = %s AND is_active
            """, (username,))
            row = cur.fetchone()
    return AdminUser(id=row[0], username=row[1], role=row[2]) if row else None


def authenticate(username: str, password: str) -> AdminUser | None:
    with db.central().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, username, role, password_hash FROM integration.admin_user
                WHERE username = %s AND is_active
            """, (username,))
            row = cur.fetchone()
    if not row or not verify_password(password, row[3]):
        return None
    return AdminUser(id=row[0], username=row[1], role=row[2])


def set_session(response: Response, user: AdminUser) -> None:
    token = _get_signer().sign(user.username.encode()).decode()
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True, samesite="lax",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)


def current_user(request: Request) -> AdminUser:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    try:
        username = _get_signer().unsign(token, max_age=SESSION_MAX_AGE_SECONDS).decode()
    except BadSignature:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    user = load_user(username)
    if not user:
        raise HTTPException(status_code=401, detail="user disabled or removed")
    return user


def require_operator(user: AdminUser = Depends(current_user)) -> AdminUser:
    return user  # both roles may operate


def require_admin(user: AdminUser = Depends(current_user)) -> AdminUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return user
