"""TOTP / Google Authenticator MFA endpoints."""

import base64
import io
from datetime import datetime, timezone

import pyotp
import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.audit import record_db_event
from app.auth import (
    decode_token,
    encrypt_secret,
    issue_tokens,
    verify_totp,
)
from app.config import MAX_FAILED_OTP
from app.database import get_db
from app.deps import get_current_user
from app.limiter import client_ip, limiter, user_or_ip
from app.models import User
from app.routers.auth import lock_account, login_event

router = APIRouter(prefix="/auth/2fa", tags=["mfa"])

APP_NAME = "LoanMitra Portal"


def _qr_base64(uri: str) -> str:
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OTPCode(BaseModel):
    otp_code: str


class ValidateRequest(BaseModel):
    mfa_token: str
    otp_code: str


class EnrollRequest(BaseModel):
    enroll_token: str


class EnrollConfirmRequest(EnrollRequest):
    otp_code: str


STAFF_ROLES = ("officer", "admin")


async def _register_failure(db: AsyncSession, user: User) -> None:
    """Count a wrong TOTP code; lock the account after MAX_FAILED_OTP. Commits immediately."""
    user.mfa_failed_attempts += 1
    if user.mfa_failed_attempts >= MAX_FAILED_OTP:
        user.mfa_failed_attempts = 0
        await lock_account(db, user, "totp")
    await db.commit()


async def _user_from_flow_token(db: AsyncSession, token: str, expected_type: str, invalid: HTTPException) -> User:
    """Resolve a short-lived login-flow token (mfa_pending / mfa_enroll) to an active, unlocked user."""
    try:
        payload = decode_token(token)
    except JWTError:
        raise invalid from None
    if payload.get("type") != expected_type or not payload.get("sub"):
        raise invalid
    user = (await db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if not user or not user.is_active or (user.locked_until and user.locked_until > _now()):
        raise invalid
    return user


async def _start_setup(db: AsyncSession, user: User) -> dict:
    if user.totp_enabled:
        raise HTTPException(400, "2FA is already enabled. Disable it first (requires a current code).")
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=APP_NAME)
    user.totp_pending_secret = encrypt_secret(secret)
    await db.commit()
    return {"qr_image_base64": _qr_base64(uri), "secret": secret}


async def _activate(db: AsyncSession, user: User, otp_code: str, setup_path: str) -> None:
    """Verify the first authenticator code against the pending secret and turn 2FA on (not committed)."""
    if user.totp_enabled:
        raise HTTPException(400, "2FA is already enabled.")
    if not user.totp_pending_secret:
        raise HTTPException(400, f"Call {setup_path} first.")
    counter = verify_totp(user.totp_pending_secret, otp_code.strip(), None)
    if counter is None:
        await _register_failure(db, user)
        raise HTTPException(400, "Invalid or expired code. Check the code and try again.")
    user.totp_secret = user.totp_pending_secret
    user.totp_pending_secret = None
    user.totp_enabled = True
    user.totp_last_counter = counter
    user.mfa_failed_attempts = 0
    record_db_event(db, event_type="2fa_enabled", user_id=user.id)


@router.post("/setup")
@limiter.limit("5/minute", key_func=user_or_ip)
async def setup_2fa(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a pending TOTP secret. 2FA stays as-is until /confirm succeeds."""
    return await _start_setup(db, current_user)


@router.post("/confirm")
@limiter.limit("10/minute", key_func=user_or_ip)
async def confirm_2fa(
    request: Request,
    body: OTPCode,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify the first code from the authenticator app and enable 2FA."""
    await _activate(db, current_user, body.otp_code, "/auth/2fa/setup")
    await db.commit()
    return {"message": "Two-factor authentication enabled successfully."}


_INVALID_ENROLL = HTTPException(401, "Enrollment session expired or invalid. Please log in again.")


@router.post("/enroll/setup")
@limiter.limit("5/minute")
async def enroll_setup(request: Request, body: EnrollRequest, db: AsyncSession = Depends(get_db)):
    """First-login enrollment for staff: password already checked, no session tokens issued yet."""
    user = await _user_from_flow_token(db, body.enroll_token, "mfa_enroll", _INVALID_ENROLL)
    return await _start_setup(db, user)


@router.post("/enroll/confirm")
@limiter.limit("10/minute")
async def enroll_confirm(request: Request, body: EnrollConfirmRequest, db: AsyncSession = Depends(get_db)):
    """Activate 2FA with the first code and complete the login in one step."""
    user = await _user_from_flow_token(db, body.enroll_token, "mfa_enroll", _INVALID_ENROLL)
    await _activate(db, user, body.otp_code, "/auth/2fa/enroll/setup")
    tokens = issue_tokens(user, new_session=True)
    login_event(db, client_ip(request), "login_succeeded", user, method="totp_enrollment")
    await db.commit()
    return tokens


@router.post("/disable")
@limiter.limit("10/minute", key_func=user_or_ip)
async def disable_2fa(
    request: Request,
    body: OTPCode,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable 2FA — requires a valid, unused code."""
    if not current_user.totp_enabled:
        raise HTTPException(400, "2FA is not enabled on this account.")
    if current_user.role in STAFF_ROLES:
        # Staff cannot log in without 2FA, so disabling it would only lock them out.
        raise HTTPException(403, "2FA is mandatory for staff accounts and cannot be disabled.")
    counter = verify_totp(current_user.totp_secret, body.otp_code.strip(), current_user.totp_last_counter)
    if counter is None:
        await _register_failure(db, current_user)
        raise HTTPException(400, "Invalid code. 2FA not disabled.")
    current_user.totp_secret = None
    current_user.totp_pending_secret = None
    current_user.totp_enabled = False
    current_user.totp_last_counter = None
    current_user.mfa_failed_attempts = 0
    record_db_event(db, event_type="2fa_disabled", user_id=current_user.id)
    await db.commit()
    return {"message": "Two-factor authentication disabled."}


@router.post("/validate")
@limiter.limit("10/minute")
async def validate_2fa(
    request: Request,
    body: ValidateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Second step of login — verify the TOTP code and issue real tokens."""
    invalid = HTTPException(401, "Invalid code or expired MFA session. Please try again or log in again.")
    user = await _user_from_flow_token(db, body.mfa_token, "mfa_pending", invalid)
    if not user.totp_enabled or not user.totp_secret:
        raise invalid

    counter = verify_totp(user.totp_secret, body.otp_code.strip(), user.totp_last_counter)
    if counter is None:
        login_event(db, client_ip(request), "login_failed", user, reason="wrong_totp")
        await _register_failure(db, user)  # commits the event too
        raise invalid

    user.totp_last_counter = counter
    user.mfa_failed_attempts = 0
    # issue_tokens embeds token_version; without it every 2FA login after a logout was rejected.
    tokens = issue_tokens(user, new_session=True)
    login_event(db, client_ip(request), "login_succeeded", user, method="totp")
    await db.commit()
    return tokens
