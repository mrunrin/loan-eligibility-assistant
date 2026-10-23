import base64
import time

import nav
import streamlit as st

PAGE = "pages/9_Security.py"

st.set_page_config(page_title="Security — Loan Portal", layout="centered")
user = nav.require_login()
nav.render_sidebar(current_page=PAGE)

st.title("Security Settings")
st.caption(f"Account: {user.get('email', '')}")
st.divider()

me = nav.json_or_none(nav.api("GET", "/auth/me", timeout=5))
if me is None:
    st.error("Could not load your account settings. Please try again.")
    st.stop()
st.session_state["user"] = me
totp_enabled = me.get("totp_enabled", False)

# ── Two-Factor Authentication ────────────────────────────────────────────────
st.subheader("Two-Factor Authentication (2FA)")

if totp_enabled and me.get("role") in ("officer", "admin"):
    st.success("2FA is enabled on your account.")
    st.caption("Two-factor authentication is mandatory for staff accounts and cannot be disabled.")
elif totp_enabled:
    st.success("2FA is enabled on your account.")
    st.write("Your account is protected with an authenticator app.")
    st.divider()
    st.subheader("Disable 2FA")
    st.warning("You will need your authenticator app to disable 2FA.")
    with st.form("disable_2fa"):
        otp = st.text_input("Enter your current 6-digit code to confirm", max_chars=6, placeholder="123456")
        submitted = st.form_submit_button("Disable 2FA", use_container_width=True)
    if submitted:
        if not otp or not otp.isdigit() or len(otp) != 6:
            st.error("Enter a valid 6-digit code.")
        else:
            resp = nav.api("POST", "/auth/2fa/disable", json={"otp_code": otp})
            if resp is not None and resp.status_code == 200:
                st.success("2FA disabled. Your account now uses password-only login.")
                st.rerun()
            elif resp is not None:
                st.error(nav.detail(resp, "Could not disable 2FA."))
else:
    st.info("2FA is not enabled. Add an extra layer of security to your account.")
    if st.button("Set up Google Authenticator", use_container_width=True, type="primary"):
        resp = nav.api("POST", "/auth/2fa/setup")
        data = nav.json_or_none(resp)
        if data:
            st.session_state["totp_setup_qr"] = data["qr_image_base64"]
        else:
            st.error(nav.detail(resp, "Could not generate a QR code. Please try again."))

    if st.session_state.get("totp_setup_qr"):
        st.divider()
        st.subheader("Scan this QR code")
        col_qr, col_inst = st.columns([1, 1])
        with col_qr:
            st.image(base64.b64decode(st.session_state["totp_setup_qr"]), width=220)
        with col_inst:
            st.markdown(
                "**Steps:**\n"
                "1. Open **Google Authenticator** (or Authy) on your phone\n"
                "2. Tap **+** → **Scan a QR code**\n"
                "3. Scan the QR code on the left\n"
                "4. Enter the 6-digit code below to confirm"
            )
        seconds_left = 30 - (time.time() % 30)
        st.caption(f"Code refreshes in {int(seconds_left)} seconds")
        st.progress(seconds_left / 30)

        with st.form("confirm_2fa"):
            otp = st.text_input("Enter the 6-digit code from the app", max_chars=6, placeholder="123456")
            confirmed = st.form_submit_button("Activate 2FA", use_container_width=True, type="primary")
        if confirmed:
            if not otp or not otp.isdigit() or len(otp) != 6:
                st.error("Enter a valid 6-digit code.")
            else:
                resp = nav.api("POST", "/auth/2fa/confirm", json={"otp_code": otp})
                if resp is not None and resp.status_code == 200:
                    del st.session_state["totp_setup_qr"]
                    st.success("2FA activated! Your account is now protected.")
                    st.rerun()
                elif resp is not None:
                    st.error(nav.detail(resp, "Invalid code. Try again."))

# ── Danger Zone ──────────────────────────────────────────────────────────────
st.divider()
st.subheader("Danger Zone")

apps = nav.json_or_none(nav.api("GET", "/applications", params={"limit": 1}))
if apps is None:
    st.warning("Could not check your applications, so account deletion is unavailable right now.")
elif apps:
    st.error(
        "Account deletion is not available because you have loan applications on record. "
        "Financial records must be retained for regulatory compliance. "
        "Contact support if you need further assistance."
    )
else:
    st.warning("Deleting your account is permanent and cannot be undone. All your personal data will be removed.")
    account_email = me.get("email", "")
    with st.form("delete_account"):
        confirm_email = st.text_input("Type your email address to confirm deletion", placeholder=account_email)
        password = st.text_input("Password", type="password")
        delete_otp = st.text_input("Authenticator code", max_chars=6) if totp_enabled else None
        delete = st.form_submit_button("Delete My Account", type="primary", use_container_width=True)
    if delete:
        if not account_email or confirm_email.strip().lower() != account_email.lower():
            st.error("The email you typed does not match your account.")
        elif not password:
            st.error("Enter your password to confirm.")
        else:
            resp = nav.api("DELETE", "/auth/me", json={"password": password, "otp_code": delete_otp})
            if resp is not None and resp.status_code == 204:
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.session_state["flash"] = "Your account has been deleted."
                st.switch_page("streamlit_app.py")
            elif resp is not None:
                st.error(nav.detail(resp, "Could not delete account. Please try again."))
