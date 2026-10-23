import base64
import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from jose import jwt
from passlib.context import CryptContext

from app.config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    DATA_ENCRYPTION_KEY,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    REFRESH_TOKEN_EXPIRE_HOURS,
    SESSION_IDLE_MINUTES,
    SESSION_MAX_HOURS,
)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# Verified against when the user does not exist, so login timing does not reveal registered emails.
_DUMMY_HASH = _pwd_context.hash("timing-equaliser-not-a-real-password")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    if hashed is None:
        _pwd_context.verify(plain, _DUMMY_HASH)
        return False
    return _pwd_context.verify(plain, hashed)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hmac_code(code: str) -> str:
    """Keyed hash for short codes (email OTP): a DB dump alone cannot brute-force them offline."""
    return hmac.new(JWT_SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def codes_match(code: str, stored_hmac: str | None) -> bool:
    return bool(stored_hmac) and hmac.compare_digest(hmac_code(code), stored_hmac)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

# Every token names who issued it and who may accept it; decode_token rejects tokens minted for anything else.
JWT_ISSUER = "hdfc-loan-portal"
JWT_AUDIENCE = "hdfc-loan-portal-api"


def _encode(payload: dict) -> str:
    return jwt.encode({**payload, "iss": JWT_ISSUER, "aud": JWT_AUDIENCE}, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)



def create_access_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload["type"] = "access"
    # token_version is embedded at issue time; get_current_user rejects tokens where the
    # DB version is higher (bumped on logout or password change).
    payload.setdefault("ver", 0)
    return _encode(payload)


def create_refresh_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=REFRESH_TOKEN_EXPIRE_HOURS)
    payload["type"] = "refresh"
    # jti keeps two refresh tokens minted in the same second distinct (rotation relies on it)
    payload["jti"] = f"{time.time_ns()}"
    return _encode(payload)


def create_mfa_pending_token(user_id: str) -> str:
    """Short-lived token (5 min) issued after password check when 2FA is required."""
    payload = {
        "sub": user_id,
        "type": "mfa_pending",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    return _encode(payload)


def create_mfa_enroll_token(user_id: str) -> str:
    """Short-lived token (10 min) issued after password check to staff who have not enrolled in 2FA yet.

    It only unlocks /auth/2fa/enroll/* — get_current_user rejects any type other than "access".
    """
    payload = {
        "sub": user_id,
        "type": "mfa_enroll",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
    }
    return _encode(payload)


def issue_tokens(user, new_session: bool = False) -> dict:
    """Access + refresh pair carrying the user's current token_version; stores the refresh hash.

    new_session=True on a completed sign-in: starts the absolute session clock and clears lockout backoff.
    """
    now = datetime.now(timezone.utc)
    if new_session or user.session_started_at is None:
        user.session_started_at = now
        user.lockout_count = 0
    user.last_seen_at = now
    token_data = {"sub": str(user.id), "role": user.role, "ver": user.token_version}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)
    user.refresh_token_hash = hash_token(refresh_token)
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises JWTError on failure."""
    return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], audience=JWT_AUDIENCE, issuer=JWT_ISSUER)


def session_expired(user, now: datetime) -> bool:
    """Server-side idle and absolute session limits; the browser idle timer alone can be bypassed."""
    if user.last_seen_at and now - user.last_seen_at > timedelta(minutes=SESSION_IDLE_MINUTES):
        return True
    return bool(user.session_started_at and now - user.session_started_at > timedelta(hours=SESSION_MAX_HOURS))


# ---------------------------------------------------------------------------
# TOTP secrets — encrypted at rest, replay-protected
# ---------------------------------------------------------------------------


def _fernet() -> Fernet:
    material = DATA_ENCRYPTION_KEY or f"totp-at-rest:{JWT_SECRET_KEY}"
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())
    return Fernet(key)


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None


def verify_totp(encrypted_secret: str | None, code: str, last_counter: int | None) -> int | None:
    """Return the matched time-step counter, or None if the code is wrong or already used."""
    if not encrypted_secret or not code or not code.isdigit():
        return None
    secret = decrypt_secret(encrypted_secret)
    if secret is None:
        return None
    totp = pyotp.TOTP(secret)
    now = datetime.now(timezone.utc)
    current = totp.timecode(now)
    for offset in (-1, 0, 1):
        counter = current + offset
        if hmac.compare_digest(totp.generate_otp(counter), code):
            if last_counter is not None and counter <= last_counter:
                return None
            return counter
    return None
