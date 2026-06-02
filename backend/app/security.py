"""Password hashing (bcrypt) and JWT issuing/verification for admin auth."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

_ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=True)


def hash_password(plain: str) -> str:
    """bcrypt hash (for seeding ADMIN_PASSWORD_HASH)."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": "admin",
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expire_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def get_current_admin(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> str:
    """FastAPI dependency: require a valid, non-expired admin JWT (Bearer)."""
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return payload.get("sub", "")


if __name__ == "__main__":  # `python -m backend.app.security <password>` → print a hash
    import sys

    pw = sys.argv[1] if len(sys.argv) > 1 else "admin"
    print(hash_password(pw))
