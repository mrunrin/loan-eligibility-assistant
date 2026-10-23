import logging
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from jose import JWTError
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.audit import record_db_event
from app.auth import (
    codes_match,
    create_mfa_enroll_token,
    create_mfa_pending_token,
    decode_token,
    hash_password,
    hash_token,
    issue_tokens,
    session_expired,
    verify_password,
    verify_totp,
)
from app.config import (
    DEV_EMAIL_VERIFY,
    IP_FAILED_LOGIN_LIMIT,
    LOCKOUT_MINUTES,
    MAX_FAILED_LOGINS,
    MAX_FAILED_OTP,
)
from app.database import get_db
from app.deps import get_current_user
from app.email_utils import (
    EMAIL_ENABLED,
    generate_verify_token,
    send_account_locked_email,
    send_verification_email,
)
from app.limiter import client_ip, limiter
from app.models import LoanApplication, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# OWASP ASVS 2.1 / PCI-DSS aligned: 8+ chars, upper, lower, digit, special char
_PASSWORD_RE = re.compile(
    r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]).{8,72}$"
)
_PASSWORD_HINT = (
    "Password must be 8–72 characters and include at least one uppercase letter, "
    "one lowercase letter, one digit, and one special character (!@#$%^&* etc.)."
)
_INVALID_LOGIN = "Invalid credentials, or the account is temporarily locked. Try again later."
_INVALID_OTP = "Invalid or expired verification code."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm_email(value: str) -> str:
    return value.strip().lower()


_MAX_LOCKOUT_MINUTES = 24 * 60


async def lock_account(db: AsyncSession, user: User, reason: str) -> None:
    """Lock with exponential backoff (15, 30, 60 … minutes, capped at 24 h) and tell the owner by email."""
    minutes = min(LOCKOUT_MINUTES * 2 ** min(user.lockout_count, 10), _MAX_LOCKOUT_MINUTES)
    user.locked_until = _now() + timedelta(minutes=minutes)
    user.lockout_count += 1
    record_db_event(db, event_type="account_locked", user_id=user.id, payload={"reason": reason, "minutes": minutes})
    await send_account_locked_email(user.email, user.full_name or "", minutes)


class _IpFailures:
    """Failed logins per client IP in a sliding LOCKOUT_MINUTES window, across all accounts.

    Stops one client from spraying passwords or locking many accounts: once over the limit it gets 429
    and its attempts no longer count against any account.
    shortcut: in-process, one backend worker only; move to Redis before running several workers.
    """

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    def _recent(self, ip: str) -> list[float]:
        cutoff = time.monotonic() - LOCKOUT_MINUTES * 60
        hits = [t for t in self._hits.pop(ip, []) if t > cutoff]
        if hits:
            self._hits[ip] = hits
        return hits

    def blocked(self, ip: str) -> bool:
        return len(self._recent(ip)) >= IP_FAILED_LOGIN_LIMIT

    def add(self, ip: str) -> None:
        self._hits[ip] = [*self._recent(ip), time.monotonic()]

    def clear(self) -> None:
        self._hits.clear()


ip_failures = _IpFailures()


def login_event(db: AsyncSession, ip: str, event_type: str, user: User | None = None, **details) -> None:
    """Audit a sign-in attempt (login_succeeded / login_failed) with the client IP and outcome details."""
    record_db_event(db, event_type=event_type, user_id=user.id if user else None, payload={"ip": ip, **details})


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return _norm_email(v)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not _PASSWORD_RE.match(v):
            raise ValueError(_PASSWORD_HINT)
        return v

    @field_validator("full_name")
    @classmethod
    def name_length(cls, v: str | None) -> str | None:
        return v.strip()[:255] if v else v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return _norm_email(v)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str | None
    role: str
    is_active: bool
    totp_enabled: bool = False
    is_email_verified: bool = False


class RefreshRequest(BaseModel):
    refresh_token: str


class OTPVerifyRequest(BaseModel):
    email: str
    otp: str

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return _norm_email(v)


class EmailOnlyRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return _norm_email(v)


class DeleteAccountRequest(BaseModel):
    password: str
    otp_code: str | None = None


def _issue_tokens(user: User, new_session: bool = False) -> TokenResponse:
    return TokenResponse(**issue_tokens(user, new_session=new_session))


async def _get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(func.lower(User.email) == email))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def register(
    request: Request,
    body: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    # Same response whether or not the email exists, so registration cannot be used to enumerate users.
    response = {"email_verification_required": True, "email": body.email}
    hashed = hash_password(body.password)  # always hash: equal timing on both paths
    if await _get_user_by_email(db, body.email):
        return response

    otp, token_hash, expires = generate_verify_token()
    db.add(User(
        email=body.email,
        hashed_password=hashed,
        full_name=body.full_name,
        role="borrower",
        is_email_verified=False,
        email_verify_token_hash=token_hash,
        email_verify_expires_at=expires,
    ))
    await db.commit()

    if EMAIL_ENABLED:
        background_tasks.add_task(send_verification_email, body.email, body.full_name or "", otp)
    return response


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    body: LoginRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    ip = client_ip(request)
    if ip_failures.blocked(ip):
        login_event(db, ip, "login_failed", reason="ip_blocked")
        await db.commit()
        raise HTTPException(status_code=429, detail="Too many failed sign-in attempts from your network. Try again later.")

    user = await _get_user_by_email(db, body.email)
    password_ok = verify_password(body.password, user.hashed_password if user else None)
    if not user or not password_ok:
        ip_failures.add(ip)
    if not user:
        # Hash, not the address: the audit trail can link repeat attempts without storing unknown emails.
        login_event(db, ip, "login_failed", reason="unknown_email", email_sha256=hash_token(body.email))
        await db.commit()
        raise HTTPException(status_code=401, detail=_INVALID_LOGIN)

    if user.locked_until and user.locked_until > _now():
        login_event(db, ip, "login_failed", user, reason="account_locked")
        await db.commit()
        raise HTTPException(status_code=401, detail=_INVALID_LOGIN)

    if not password_ok:
        login_event(db, ip, "login_failed", user, reason="wrong_password")
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= MAX_FAILED_LOGINS:
            user.failed_login_attempts = 0
            await lock_account(db, user, "password")
        await db.commit()  # get_db rolls back on exceptions; persist the counter first
        raise HTTPException(status_code=401, detail=_INVALID_LOGIN)

    user.failed_login_attempts = 0
    if not user.is_active:
        login_event(db, ip, "login_failed", user, reason="inactive")
        await db.commit()
        raise HTTPException(status_code=403, detail="Account is inactive.")
    if not user.is_email_verified:
        login_event(db, ip, "login_failed", user, reason="email_unverified")
        if EMAIL_ENABLED and not user.email_verify_token_hash:
            otp, token_hash, expires = generate_verify_token()
            user.email_verify_token_hash = token_hash
            user.email_verify_expires_at = expires
            user.email_verify_attempts = 0
            background_tasks.add_task(send_verification_email, user.email, user.full_name or "", otp)
        await db.commit()
        raise HTTPException(
            status_code=403,
            detail="Email not verified. Please verify your email before logging in.",
        )

    if user.role in ("officer", "admin") and not user.totp_enabled:
        # Password is correct but staff need 2FA: hand out an enrollment-only token, never session tokens.
        await db.commit()
        return JSONResponse({"mfa_enrollment_required": True, "enroll_token": create_mfa_enroll_token(str(user.id))})

    if user.totp_enabled:
        await db.commit()
        return JSONResponse({"mfa_required": True, "mfa_token": create_mfa_pending_token(str(user.id))})

    tokens = _issue_tokens(user, new_session=True)
    login_event(db, ip, "login_succeeded", user, method="password")
    await db.commit()
    return tokens


@router.post("/logout")
async def logout(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    current_user.refresh_token_hash = None
    current_user.token_version = (current_user.token_version or 0) + 1
    await db.commit()
    return {"message": "Logged out."}


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("30/minute")
async def refresh(request: Request, body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    credentials_exc = HTTPException(status_code=401, detail="Invalid refresh token.")
    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") != "refresh" or not payload.get("sub"):
            raise credentials_exc
        user_id: str = payload["sub"]
    except JWTError:
        raise credentials_exc from None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise credentials_exc

    if user.refresh_token_hash != hash_token(body.refresh_token):
        # A validly signed but already-rotated token: likely theft. Revoke the live session too.
        if user.refresh_token_hash is not None:
            user.refresh_token_hash = None
            record_db_event(db, event_type="refresh_token_reuse", user_id=user.id)
            await db.commit()
            logger.warning("Refresh token reuse detected for user %s; session revoked", user.id)
        raise credentials_exc

    if session_expired(user, _now()):
        # Idle too long or past the absolute session length: end the session; the user signs in again.
        user.refresh_token_hash = None
        await db.commit()
        raise credentials_exc

    tokens = _issue_tokens(user)
    await db.commit()
    return tokens


@router.post("/verify-email")
@limiter.limit("10/minute")
async def verify_email(request: Request, body: OTPVerifyRequest, db: AsyncSession = Depends(get_db)):
    invalid = HTTPException(status_code=400, detail=_INVALID_OTP)
    user = await _get_user_by_email(db, body.email)
    if not user or user.is_email_verified or not user.email_verify_token_hash:
        raise invalid
    if user.email_verify_expires_at and user.email_verify_expires_at < _now():
        raise invalid
    if not codes_match(body.otp.strip(), user.email_verify_token_hash):
        user.email_verify_attempts += 1
        if user.email_verify_attempts >= MAX_FAILED_OTP:
            # Burn the code; the user must request a new one.
            user.email_verify_token_hash = None
            user.email_verify_expires_at = None
            user.email_verify_attempts = 0
        await db.commit()
        raise invalid
    user.is_email_verified = True
    user.email_verify_token_hash = None
    user.email_verify_expires_at = None
    user.email_verify_attempts = 0
    await db.commit()
    return {"message": "Email verified successfully. Please log in."}


@router.post("/resend-verification")
@limiter.limit("3/4hour")
async def resend_verification(
    request: Request,
    body: EmailOnlyRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    if not EMAIL_ENABLED:
        raise HTTPException(status_code=503, detail="Email is not configured on this server. Contact the administrator.")
    user = await _get_user_by_email(db, body.email)
    if user and not user.is_email_verified:
        otp, token_hash, expires = generate_verify_token()
        user.email_verify_token_hash = token_hash
        user.email_verify_expires_at = expires
        user.email_verify_attempts = 0
        await db.commit()
        background_tasks.add_task(send_verification_email, user.email, user.full_name or "", otp)
    return {"message": "If that email exists and is unverified, a new code has been sent."}


@router.post("/dev-verify")
@limiter.limit("10/minute")
async def dev_verify(request: Request, body: EmailOnlyRequest, db: AsyncSession = Depends(get_db)):
    """Dev-only instant verification; exists only when DEV_EMAIL_VERIFY=true and SMTP is off."""
    if not DEV_EMAIL_VERIFY or EMAIL_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    user = await _get_user_by_email(db, body.email)
    if user and not user.is_email_verified:
        user.is_email_verified = True
        user.email_verify_token_hash = None
        user.email_verify_expires_at = None
        await db.commit()
    return {"message": "If that account exists, it is now verified. Please log in."}


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    body: DeleteAccountRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete the account. Requires the password (and a TOTP code when 2FA is on)."""
    if not verify_password(body.password, current_user.hashed_password):
        raise HTTPException(status_code=403, detail="Password is incorrect.")
    if current_user.totp_enabled:
        counter = verify_totp(current_user.totp_secret, (body.otp_code or "").strip(), current_user.totp_last_counter)
        if counter is None:
            raise HTTPException(status_code=403, detail="A valid authenticator code is required.")
    has_apps = await db.execute(
        select(LoanApplication.id).where(LoanApplication.applicant_id == current_user.id).limit(1)
    )
    if has_apps.first():
        raise HTTPException(
            status_code=409,
            detail="Accounts with loan applications on record cannot be deleted (records must be retained).",
        )
    record_db_event(db, event_type="account_deleted", payload={"user_id": str(current_user.id)})
    await db.delete(current_user)
    await db.commit()


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return UserOut(
        id=str(current_user.id),
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role,
        is_active=current_user.is_active,
        totp_enabled=current_user.totp_enabled,
        is_email_verified=current_user.is_email_verified,
    )
