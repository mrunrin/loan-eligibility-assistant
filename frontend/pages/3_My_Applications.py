import nav
import streamlit as st

PAGE = "pages/3_My_Applications.py"
PAGE_SIZE = 20

st.set_page_config(page_title="My Applications — Loan Portal", layout="wide")
user = nav.require_login()
nav.render_sidebar(current_page=PAGE)

st.title("My Applications")
st.write(f"Welcome, **{user.get('full_name') or user.get('email', '')}** ({user.get('role', '')})")

col1, col2 = st.columns([6, 1])
with col2:
    if user.get("role") == "borrower" and st.button("+ New Application"):
        st.switch_page("pages/4_Apply.py")

offset = st.session_state.setdefault("apps_offset", 0)
resp = nav.api("GET", "/applications", params={"limit": PAGE_SIZE, "offset": offset})
apps = nav.json_or_none(resp)
if apps is None:
    if resp is not None:
        st.error(nav.detail(resp, "Could not load applications."))
    st.stop()

STATUS_COLORS = {
    "draft": "gray",
    "submitted": "blue",
    "pre_qual_pass": "green",
    "pre_qual_fail": "red",
    "manual_review": "orange",
    "under_review": "orange",
    "approved": "green",
    "rejected": "red",
    "withdrawn": "gray",
}

CHAT_STATUSES = {"pre_qual_fail", "manual_review", "under_review", "rejected"}

if not apps and offset == 0:
    st.info("No applications yet. Click **+ New Application** to get started.")
for app in apps:
    status = app["status"]
    show_chat = status in CHAT_STATUSES
    c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 1, 1])
    c1.write(f"**{app.get('title') or 'Untitled'}**")
    c2.markdown(f":{STATUS_COLORS.get(status, 'gray')}[{status.replace('_', ' ').title()}]")
    amount = app.get("loan_amount_requested")
    c3.write(f"₹{amount:,.0f}" if amount is not None else "—")
    if c4.button("View", key=f"view_{app['id']}"):
        st.session_state["selected_app_id"] = app["id"]
        st.switch_page("pages/5_Application_Detail.py")
    if show_chat and c5.button("💬 Chat", key=f"chat_{app['id']}"):
        st.session_state["chat_app_id"] = app["id"]
        st.session_state["chat_app_name"] = app.get("title") or "Untitled"
        st.switch_page("pages/8_Chat_Assistant.py")
    elif not show_chat:
        c5.empty()
    st.divider()

prev_col, _, next_col = st.columns([1, 4, 1])
if offset > 0 and prev_col.button("← Previous"):
    st.session_state["apps_offset"] = max(0, offset - PAGE_SIZE)
    st.rerun()
if len(apps) == PAGE_SIZE and next_col.button("Next →"):
    st.session_state["apps_offset"] = offset + PAGE_SIZE
    st.rerun()
