import nav
import streamlit as st

st.set_page_config(page_title="Register — Loan Portal", layout="centered", initial_sidebar_state="collapsed")
nav.hide_sidebar()

if st.session_state.get("access_token"):
    nav.redirect_to_dashboard()

st.title("Create Account")

_PW_HINT = "8–72 characters · uppercase · lowercase · digit · special char (!@#$%^&* etc.)"

with st.form("register_form"):
    full_name = st.text_input("Full Name")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password", help=_PW_HINT)
    confirm = st.text_input("Confirm Password", type="password")
    submitted = st.form_submit_button("Register", use_container_width=True, type="primary")

if submitted:
    if not full_name or not email or not password:
        st.error("All fields are required.")
    elif password != confirm:
        st.error("Passwords do not match.")
    else:
        resp = nav.api("POST", "/auth/register", json={"email": email, "password": password, "full_name": full_name})
        if resp is not None and resp.status_code == 201:
            st.session_state["pending_verify_email"] = email.strip().lower()
            st.rerun()
        elif resp is not None and resp.status_code == 429:
            st.error("Too many registration attempts. Please wait a minute and try again.")
        elif resp is not None:
            st.error(nav.detail(resp, "Registration failed. Please check your inputs and try again."))

if st.session_state.get("pending_verify_email"):
    pending_email = st.session_state["pending_verify_email"]
    health = nav.backend_health()

    if health.get("dev_email_verify"):
        st.success(f"Account created for **{pending_email}**.")
        st.info("Email (SMTP) is not configured on this server. Dev mode allows instant verification.")
        if st.button("Verify instantly (dev mode)", use_container_width=True, type="primary"):
            resp = nav.api("POST", "/auth/dev-verify", json={"email": pending_email})
            if resp is not None and resp.status_code == 200:
                del st.session_state["pending_verify_email"]
                st.session_state["flash"] = "Email verified! Please log in."
                st.switch_page("streamlit_app.py")
            elif resp is not None:
                st.error(nav.detail(resp, "Verification failed."))
    else:
        st.success(f"If this email is new, a 6-digit code has been sent to **{pending_email}**.")
        st.info("Check your inbox (and spam folder). The code expires in **15 minutes**.")
        col_otp, col_send = st.columns([3, 2])
        with col_otp:
            otp_input = st.text_input("Enter code", max_chars=6, placeholder="123456", label_visibility="collapsed")
        with col_send:
            if st.button("Resend code", use_container_width=True):
                resp = nav.api("POST", "/auth/resend-verification", json={"email": pending_email})
                if resp is not None and resp.status_code == 200:
                    st.success("If the account is unverified, a new code has been sent.")
                elif resp is not None and resp.status_code == 429:
                    st.error("Too many requests. Please wait before asking for another code.")
                elif resp is not None:
                    st.error(nav.detail(resp, "Could not send a code."))

        if st.button("Verify →", use_container_width=True, type="primary"):
            if not otp_input or len(otp_input) != 6 or not otp_input.isdigit():
                st.error("Enter the 6-digit code from your email.")
            else:
                resp = nav.api("POST", "/auth/verify-email", json={"email": pending_email, "otp": otp_input})
                if resp is not None and resp.status_code == 200:
                    del st.session_state["pending_verify_email"]
                    st.session_state["flash"] = "Email verified! Please log in."
                    st.switch_page("streamlit_app.py")
                elif resp is not None:
                    st.error(nav.detail(resp, "Incorrect code. Please try again."))

st.markdown("---")
if st.button("← Back to Login"):
    st.switch_page("streamlit_app.py")
