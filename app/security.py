import hashlib
import secrets
import time
from collections import defaultdict

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User

_ph = PasswordHasher()
_DUMMY_HASH = _ph.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _ph.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _ph.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def temp_password(length: int = 12) -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


class LoginRequired(Exception):
    pass


class PasswordChangeRequired(Exception):
    pass


class Forbidden(Exception):
    pass


ALLOWED_WHILE_FORCED = {"/account/password", "/logout", "/admin/stop-impersonation"}


def load_session_user(request: Request, db: Session) -> User | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    user = db.get(User, uid)
    if user is None or user.status != "active" or user.session_version != request.session.get("sv"):
        request.session.clear()
        return None
    return user


def login_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session.update({"uid": user.id, "sv": user.session_version})


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = load_session_user(request, db)
    if user is None:
        raise LoginRequired()
    request.state.user = user
    if user.must_change_password and request.url.path not in ALLOWED_WHILE_FORCED:
        raise PasswordChangeRequired()
    return user


def require_founder(user: User = Depends(current_user)) -> User:
    if not user.is_founder:
        raise Forbidden()
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise Forbidden()
    return user


class RateLimiter:
    """Tiny in-memory limiter (single process is fine for the MVP)."""

    def __init__(self):
        self._hits: dict[str, list[float]] = defaultdict(list)

    def blocked(self, key: str, limit: int = 10, window: int = 900) -> bool:
        now = time.time()
        self._hits[key] = [t for t in self._hits[key] if now - t < window]
        return len(self._hits[key]) >= limit

    def hit(self, key: str) -> None:
        self._hits[key].append(time.time())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


limiter = RateLimiter()
