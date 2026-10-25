import base64
import json
import time
from pathlib import Path

import extra_streamlit_components as stx
import nav
import streamlit as st

st.set_page_config(
    page_title="LoanMitra Portal",
    page_icon="🏦",
    layout="centered",
    initial_sidebar_state="collapsed",
)
nav.hide_sidebar()

# ── Persistent session: restore tokens from cookies on browser refresh ───────
_cm = stx.CookieManager(key="hdfc_portal_auth")
nav._cookie_manager = _cm  # expose to nav so logout can clear cookies

if not st.session_state.get("access_token"):
    if not st.session_state.get("_cookie_init"):
        # CookieManager needs one render cycle for its iframe to load.
        # Rerun silently so the next render gets real cookie values.
        st.session_state["_cookie_init"] = True
        st.rerun()
    _at = _cm.get("hdfc_at")
    _rt = _cm.get("hdfc_rt")
    if _at and _rt:
        st.session_state["access_token"] = _at
        st.session_state["refresh_token"] = _rt
        me = nav.json_or_none(nav.api("GET", "/auth/me", timeout=5))
        if me:
            st.session_state["user"] = me
            nav.redirect_to_dashboard()
        else:
            _cm.delete("hdfc_at")
            _cm.delete("hdfc_rt")

# Clear cookies on logout redirect or idle timeout
if st.query_params.get("action") == "logout" or st.query_params.get("idle") == "1":
    _idle = st.query_params.get("idle") == "1"
    _cm.delete("hdfc_at")
    _cm.delete("hdfc_rt")
    nav._clear_session()
    st.query_params.clear()
    if _idle:
        st.warning("You were logged out due to 5 minutes of inactivity.")

if st.session_state.get("access_token"):
    nav.redirect_to_dashboard()

st.markdown(
    """
    <div style="text-align:center; padding: 2rem 0 1rem;">
        <h1 style="font-size:2.4rem; margin-bottom:0.2rem;">🏦 LoanMitra Portal</h1>
        <p style="color:gray; font-size:1rem;">Smarter lending, faster decisions</p>
    </div>
    """,
    unsafe_allow_html=True,
)

if flash := st.session_state.pop("flash", None):
    st.success(flash)


def _finish_login(data: dict) -> None:
    st.session_state["access_token"] = data["access_token"]
    st.session_state["refresh_token"] = data["refresh_token"]
    me = nav.json_or_none(nav.api("GET", "/auth/me", timeout=5))
    if me is None:
        st.error("Logged in, but your profile could not be loaded. Please try again.")
        return
    st.session_state["user"] = me
    # Persist tokens to browser cookies so refresh doesn't log the user out
    _cm.set("hdfc_at", data["access_token"], max_age=300)     # 5 min — matches ACCESS_TOKEN_EXPIRE_MINUTES
    _cm.set("hdfc_rt", data["refresh_token"], max_age=3600)   # 1 hour — matches REFRESH_TOKEN_EXPIRE_HOURS
    nav.redirect_to_dashboard()


def _verification_panel(email: str) -> None:
    st.markdown("---")
    st.subheader("Verify your email")
    health = nav.backend_health()
    if health.get("dev_email_verify"):
        st.info("Email (SMTP) is not configured on this server. Dev mode allows instant verification.")
        if st.button("Verify instantly (dev mode)", use_container_width=True, type="primary"):
            resp = nav.api("POST", "/auth/dev-verify", json={"email": email})
            if resp is not None and resp.status_code == 200:
                st.session_state.pop("unverified_email", None)
                st.success("Email verified. Please log in.")
            elif resp is not None:
                st.error(nav.detail(resp, "Verification failed."))
        return

    st.caption(f"Enter the 6-digit code sent to **{email}**")
    col_otp, col_send = st.columns([3, 2])
    with col_otp:
        otp_input = st.text_input("One-time code", max_chars=6, placeholder="123456", label_visibility="collapsed")
    with col_send:
        if st.button("Send code", use_container_width=True):
            resp = nav.api("POST", "/auth/resend-verification", json={"email": email})
            if resp is not None and resp.status_code == 200:
                st.success("Code sent — check your inbox.")
            elif resp is not None and resp.status_code == 429:
                st.error("Too many requests. Please wait before asking for another code.")
            elif resp is not None:
                st.error(nav.detail(resp, "Could not send a code."))

    if st.button("Verify →", use_container_width=True, type="primary"):
        if not otp_input or len(otp_input) != 6 or not otp_input.isdigit():
            st.error("Enter the 6-digit code from your email.")
            return
        resp = nav.api("POST", "/auth/verify-email", json={"email": email, "otp": otp_input})
        if resp is not None and resp.status_code == 200:
            st.session_state.pop("unverified_email", None)
            st.session_state["flash"] = "Email verified! Please log in."
            st.rerun()
        elif resp is not None:
            st.error(nav.detail(resp, "Verification failed."))


def _code_timer() -> None:
    seconds_left = 30 - (time.time() % 30)
    st.caption(f"Code refreshes in {int(seconds_left)} seconds")
    st.progress(seconds_left / 30)


def _enrollment_panel() -> None:
    """First staff login: scan the QR, confirm one code, and get signed in."""
    token = st.session_state["enroll_token"]
    st.info("🔐 Staff accounts require two-factor authentication. Set it up once to continue.")
    if "enroll_setup" not in st.session_state:
        resp = nav.api("POST", "/auth/2fa/enroll/setup", json={"enroll_token": token})
        data = nav.json_or_none(resp)
        if data is None:
            st.error(nav.detail(resp, "Could not start 2FA setup. Please log in again."))
            del st.session_state["enroll_token"]
            return
        st.session_state["enroll_setup"] = data

    setup = st.session_state["enroll_setup"]
    col_qr, col_steps = st.columns(2)
    col_qr.image(base64.b64decode(setup["qr_image_base64"]), width=220)
    col_steps.markdown(
        "1. Open **Google Authenticator** or **Authy** on your phone\n"
        "2. Tap **+** → **Scan a QR code** and scan this code\n"
        "3. Enter the 6-digit code it shows below"
    )
    with col_steps.expander("Can't scan? Enter the key manually"):
        st.code(setup["secret"], language=None)
    _code_timer()

    with st.form("enroll_form"):
        otp = st.text_input("6-digit code from your authenticator app", max_chars=6, placeholder="123456")
        col1, col2 = st.columns(2)
        activate = col1.form_submit_button("Activate & Log In", use_container_width=True, type="primary")
        cancel = col2.form_submit_button("← Cancel", use_container_width=True)

    if cancel:
        st.session_state.pop("enroll_token", None)
        st.session_state.pop("enroll_setup", None)
        st.rerun()
    if activate:
        if not otp or len(otp) != 6 or not otp.isdigit():
            st.error("Enter a valid 6-digit code.")
            return
        resp = nav.api("POST", "/auth/2fa/enroll/confirm", json={"enroll_token": token, "otp_code": otp})
        if resp is not None and resp.status_code == 200:
            st.session_state.pop("enroll_token", None)
            st.session_state.pop("enroll_setup", None)
            _finish_login(resp.json())
        elif resp is not None and resp.status_code == 401:
            st.session_state.pop("enroll_token", None)
            st.session_state.pop("enroll_setup", None)
            st.error(nav.detail(resp, "Setup session expired. Please log in again."))
        elif resp is not None and resp.status_code == 429:
            st.error("Too many attempts. Please wait a minute and try again.")
        elif resp is not None:
            st.error(nav.detail(resp, "Invalid code."))


# ── Step 1b: first-login 2FA enrollment (staff) ──────────────────────────────
if st.session_state.get("enroll_token"):
    _enrollment_panel()

# ── Step 1: credentials ──────────────────────────────────────────────────────
elif not st.session_state.get("mfa_token"):
    with st.form("login_form"):
        email = st.text_input("Email address", placeholder="you@example.com")
        password = st.text_input("Password", type="password", placeholder="••••••••")
        submitted = st.form_submit_button("Log In", use_container_width=True, type="primary")

    if submitted:
        if not email or not password:
            st.error("Email and password are required.")
        else:
            resp = nav.api("POST", "/auth/login", json={"email": email, "password": password})
            if resp is not None:
                body = nav.json_or_none(resp) or {}
                if resp.status_code == 200 and body.get("mfa_required"):
                    st.session_state["mfa_token"] = body["mfa_token"]
                    st.rerun()
                elif resp.status_code == 200 and body.get("mfa_enrollment_required"):
                    st.session_state["enroll_token"] = body["enroll_token"]
                    st.session_state.pop("enroll_setup", None)
                    st.rerun()
                elif resp.status_code == 200 and body.get("access_token"):
                    _finish_login(body)
                elif resp.status_code == 429:
                    st.error(nav.detail(resp, "Too many login attempts. Please wait a minute and try again."))
                elif resp.status_code == 403 and "verify your email" in nav.detail(resp).lower():
                    st.warning(nav.detail(resp))
                    st.session_state["unverified_email"] = email
                else:
                    st.error(nav.detail(resp, "Invalid credentials."))

    if st.session_state.get("unverified_email"):
        _verification_panel(st.session_state["unverified_email"])

# ── Step 2: TOTP code ────────────────────────────────────────────────────────
else:
    st.info("🔐 Two-factor authentication required")
    _code_timer()

    with st.form("mfa_form"):
        otp = st.text_input("Enter the 6-digit code from your authenticator app", max_chars=6, placeholder="123456")
        col1, col2 = st.columns(2)
        with col1:
            verify = st.form_submit_button("Verify", use_container_width=True, type="primary")
        with col2:
            cancel = st.form_submit_button("← Cancel", use_container_width=True)

    if cancel:
        del st.session_state["mfa_token"]
        st.rerun()

    if verify:
        if not otp or len(otp) != 6 or not otp.isdigit():
            st.error("Enter a valid 6-digit code.")
        else:
            resp = nav.api("POST", "/auth/2fa/validate", json={"mfa_token": st.session_state["mfa_token"], "otp_code": otp})
            if resp is not None and resp.status_code == 200:
                del st.session_state["mfa_token"]
                _finish_login(resp.json())
            elif resp is not None and resp.status_code == 429:
                st.error("Too many attempts. Please wait a minute and try again.")
            elif resp is not None:
                st.error(nav.detail(resp, "Invalid code."))

st.markdown("<br>", unsafe_allow_html=True)

if st.button("New applicant? Register here →", use_container_width=True):
    st.switch_page("pages/2_Register.py")

# ---------------------------------------------------------------------------
# FAQ section (silent if the file is missing)
# ---------------------------------------------------------------------------
_faq_file = Path(__file__).parent / "data" / "landing_faq.json"
if _faq_file.exists():
    st.divider()
    st.subheader("Frequently Asked Questions")
    items = json.loads(_faq_file.read_text(encoding="utf-8"))
    half = (len(items) + 1) // 2
    col_l, col_r = st.columns(2)
    for i, item in enumerate(items):
        col = col_l if i < half else col_r
        with col:
            with st.expander(item["question"]):
                st.write(item["answer"])
