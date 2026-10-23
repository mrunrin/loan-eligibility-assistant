import nav
import pandas as pd
import streamlit as st

PAGE = "pages/7_Admin_Dashboard.py"
PAGE_SIZE = 50
ROLES = ["borrower", "officer", "admin"]

st.set_page_config(page_title="Admin Dashboard — Loan Portal", layout="wide")
user = nav.require_role("admin")
nav.render_sidebar(current_page=PAGE)

st.title("Admin Dashboard")

# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
stats_resp = nav.api("GET", "/admin/stats")
stats = nav.json_or_none(stats_resp)
if stats is None:
    st.error(nav.detail(stats_resp, "Could not load stats."))
else:
    by_status = stats.get("applications_by_status", {})
    by_role = stats.get("users_by_role", {})

    st.subheader("Application Overview")
    if by_status:
        cols = st.columns(len(by_status))
        for col, (status, count) in zip(cols, by_status.items(), strict=False):
            col.metric(status.replace("_", " ").title(), count)
        st.subheader("Applications by Status")
        st.bar_chart(pd.DataFrame(list(by_status.items()), columns=["Status", "Count"]).set_index("Status"))
    else:
        st.info("No applications yet.")

    st.subheader("Users by Role")
    for role, count in by_role.items():
        st.write(f"- **{role.title()}:** {count}")

st.divider()

# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------
st.subheader("User Management")

skip = st.session_state.setdefault("admin_users_skip", 0)
users_resp = nav.api("GET", "/admin/users", params={"skip": skip, "limit": PAGE_SIZE})
users = nav.json_or_none(users_resp)
if users is None:
    st.error(nav.detail(users_resp, "Could not load users."))
    st.stop()

for u in users:
    col1, col2, col3, col4 = st.columns([3, 2, 2, 2])
    col1.write(f"**{u['full_name'] or u['email']}**  \n`{u['email']}`")
    col2.write(u["role"].title())
    col3.write("Active" if u["is_active"] else "Inactive")
    if u["id"] == user.get("id"):
        col4.caption("(you)")
    else:
        new_role = col4.selectbox(
            "Change role", ROLES, index=ROLES.index(u["role"]), key=f"role_sel_{u['id']}", label_visibility="collapsed",
        )
        if new_role != u["role"] and st.button("Update", key=f"role_btn_{u['id']}"):
            update_resp = nav.api("POST", f"/admin/users/{u['id']}/role", json={"role": new_role})
            if update_resp is not None and update_resp.status_code == 200:
                st.success(f"Role updated for {u['email']}")
                st.rerun()
            elif update_resp is not None:
                st.error(nav.detail(update_resp, "Role update failed."))
    st.divider()

prev_col, _, next_col = st.columns([1, 4, 1])
if skip > 0 and prev_col.button("← Previous"):
    st.session_state["admin_users_skip"] = max(0, skip - PAGE_SIZE)
    st.rerun()
if len(users) == PAGE_SIZE and next_col.button("Next →"):
    st.session_state["admin_users_skip"] = skip + PAGE_SIZE
    st.rerun()
