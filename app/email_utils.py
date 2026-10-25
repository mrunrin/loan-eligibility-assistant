import html
import logging
import secrets
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.auth import hmac_code
from app.config import SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER

logger = logging.getLogger(__name__)

EMAIL_ENABLED = bool(SMTP_USER and SMTP_PASSWORD)


def generate_verify_token() -> tuple[str, str, datetime]:
    """Returns (otp, keyed_hash, expires_at). Store only the hash, send otp to user."""
    otp = f"{secrets.randbelow(1_000_000):06d}"
    return otp, hmac_code(otp), datetime.now(timezone.utc) + timedelta(minutes=15)


async def _send(to_email: str, subject: str, body_html: str) -> None:
    if not EMAIL_ENABLED:
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"LoanMitra <{SMTP_FROM or SMTP_USER}>"
    msg["To"] = to_email
    msg.attach(MIMEText(body_html, "html"))
    try:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER,
            password=SMTP_PASSWORD,
            start_tls=True,
        )
    except aiosmtplib.SMTPException:
        logger.exception("Email %r could not be sent", subject)


async def send_account_locked_email(to_email: str, full_name: str, minutes: int) -> None:
    await _send(to_email, "Your LoanMitra Portal account was locked", f"""<html><body style="font-family:sans-serif;max-width:480px;margin:auto;padding:24px">
<h2 style="color:#b42318">Account temporarily locked</h2>
<p>Hi {html.escape(full_name) or 'there'},</p>
<p>We locked your account for <strong>{minutes} minutes</strong> after several failed sign-in attempts.</p>
<p>If this was you, wait and try again. If it was not, someone may know your email address; consider
changing your password and turning on two-factor authentication under Security Settings.</p>
<hr style="border:none;border-top:1px solid #eee;margin-top:32px">
<p style="color:#bbb;font-size:11px">LoanMitra Portal · Smarter lending, faster decisions<br>
Student capstone demo — not affiliated with or endorsed by HDFC Bank Ltd.</p>
</body></html>""")


_STATUS_TEXT = {
    "pre_qual_pass": "passed pre-qualification and is waiting for a loan officer",
    "manual_review": "needs a manual review by a loan officer",
    "pre_qual_fail": "did not pass pre-qualification",
    "under_review": "is now being reviewed by a loan officer",
    "approved": "has been approved",
    "rejected": "has been rejected",
    "withdrawn": "has been withdrawn at your request",
}


async def send_status_email(to_email: str, full_name: str, title: str, status: str, details: list[str]) -> None:
    """Tell the borrower their application changed status, with the reasons in writing (RBI Fair Practices Code)."""
    items = "".join(f"<li>{html.escape(d)}</li>" for d in details)
    appeal = (
        "<p>You can appeal this result from the application page in the portal; "
        "a loan officer will then review it manually.</p>"
        if status == "pre_qual_fail" else ""
    )
    await _send(to_email, f"Your application: {title}", f"""<html><body style="font-family:sans-serif;max-width:480px;margin:auto;padding:24px">
<h2 style="color:#1a56db">Application update</h2>
<p>Hi {html.escape(full_name) or 'there'},</p>
<p>Your application <strong>{html.escape(title)}</strong> {_STATUS_TEXT.get(status, f"is now {html.escape(status)}")}.</p>
{f"<p>Reasons:</p><ul>{items}</ul>" if items else ""}
{appeal}
<hr style="border:none;border-top:1px solid #eee;margin-top:32px">
<p style="color:#bbb;font-size:11px">LoanMitra Portal · Smarter lending, faster decisions<br>
Student capstone demo — not affiliated with or endorsed by HDFC Bank Ltd.</p>
</body></html>""")


async def send_verification_email(to_email: str, full_name: str, otp: str) -> None:
    await _send(to_email, "Your LoanMitra Portal verification code", f"""<html><body style="font-family:sans-serif;max-width:480px;margin:auto;padding:24px">
<h2 style="color:#1a56db">LoanMitra Portal — Email Verification</h2>
<p>Hi {html.escape(full_name) or 'there'},</p>
<p>Use the code below to verify your email address. It expires in <strong>15 minutes</strong>.</p>
<div style="margin:32px auto;text-align:center;background:#f3f6ff;border-radius:10px;padding:24px;width:fit-content">
  <span style="font-size:40px;font-weight:700;letter-spacing:10px;color:#1a56db">{otp}</span>
</div>
<p style="color:#666">Enter this code on the login page to verify your email.</p>
<p style="color:#999;font-size:11px">If you didn't create this account, ignore this email.</p>
<hr style="border:none;border-top:1px solid #eee;margin-top:32px">
<p style="color:#bbb;font-size:11px">LoanMitra Portal · Smarter lending, faster decisions<br>
Student capstone demo — not affiliated with or endorsed by HDFC Bank Ltd.</p>
</body></html>""")
