from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth import decode_token, session_expired
from app.database import get_db
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise credentials_exc
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exc
    except JWTError:
        raise credentials_exc from None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    # Tokens are only issued to verified users, but re-check so a DB-level change takes effect.
    if user is None or not user.is_active or not user.is_email_verified:
        raise credentials_exc
    # token_version check: logout and password-change bump the DB value, invalidating all older tokens
    if payload.get("ver", 0) != user.token_version:
        raise credentials_exc
    now = datetime.now(timezone.utc)
    if session_expired(user, now):
        raise credentials_exc
    if user.last_seen_at is None or now - user.last_seen_at > timedelta(minutes=1):
        user.last_seen_at = now  # at most one write per user per minute; get_db commits it
    return user


def require_role(*roles: str):
    """Dependency factory — raises 403 if the current user's role is not in *roles*."""
    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorised for this action.",
            )
        return current_user

    return _check
