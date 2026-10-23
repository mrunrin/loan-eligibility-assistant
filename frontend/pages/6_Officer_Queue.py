import nav
import streamlit as st

PAGE = "pages/6_Officer_Queue.py"
PAGE_SIZE = 20
STATUS_LABELS = {
    "under_review": ":blue[In review]",
    "pre_qual_pass": ":green[Pre-qual passed]",
    "manual_review": ":orange[Manual review]",
}

st.set_page_config(page_title="Officer Queue — Loan Portal", layout="wide")
user = nav.require_role("officer", "admin")
nav.render_sidebar(current_page=PAGE)

st.title("Officer Review Queue")
st.caption("Your reviews in progress, then applications waiting for an officer. Applications another officer has started are not shown.")
if msg := st.session_state.pop("flash_queue", None):
    st.success(msg)


def _open(app_id: str) -> None:
    st.session_state["selected_app_id"] = app_id
    st.switch_page("pages/5_Application_Detail.py")


def _decisions_tab(decision: str) -> None:
    resp = nav.api("GET", "/officer/decisions", params={"decision": decision, "limit": 100})
    rows = nav.json_or_none(resp)
    if rows is None:
        st.error(nav.detail(resp, "Could not load your decisions."))
        return
    if not rows:
        st.info(f"You have not {decision} any applications yet.")
        return
    st.caption(f"{len(rows)} application{'s' if len(rows) != 1 else ''}, newest decision first.")
    for row in rows:
        amount = row.get("loan_amount_requested")
        with st.container(border=True):
            c1, c2, c3 = st.columns([5, 2, 2], vertical_alignment="center")
            c1.markdown(f"**{row['title']}**  \n:gray[Decided {(row.get('reviewed_at') or '')[:10]}]")
            c2.write(f"₹{amount:,.0f}" if amount is not None else "—")
            if c3.button("Open", key=f"{decision}_{row['id']}", use_container_width=True):
                _open(row["id"])
            st.caption(f"Reason: {row.get('review_notes') or '— (decided before reasons were mandatory)'}")


tab_queue, tab_approved, tab_rejected = st.tabs(["To review", "✅ Approved by me", "❌ Rejected by me"])
with tab_approved:
    _decisions_tab("approved")
with tab_rejected:
    _decisions_tab("rejected")

with tab_queue:
    offset = st.session_state.setdefault("queue_offset", 0)
    resp = nav.api("GET", "/officer/queue", params={"limit": PAGE_SIZE, "offset": offset})
    apps = nav.json_or_none(resp)
    if apps is None:
        if resp is not None:
            st.error(nav.detail(resp, "Could not load queue."))
        st.stop()
    if not apps and offset == 0:
        st.info("Queue is empty — no applications awaiting review.")

    for app in apps:
        app_id = app["id"]
        amount = app.get("loan_amount_requested")
        mine = app.get("assigned_to_me")
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([4, 2, 2, 2], vertical_alignment="center")
            c1.markdown(f"**{app.get('title') or 'Untitled'}**  \n:gray[Submitted {(app.get('created_at') or '')[:10]}]")
            c2.markdown(STATUS_LABELS.get(app["status"], app["status"]))
            c3.write(f"₹{amount:,.0f}" if amount is not None else "—")
            if app["status"] == "under_review" and (mine or not app.get("claim_expired")):
                # Officers see their own in-review items plus abandoned ones (below); admins see every claim.
                if c4.button("Continue review" if mine else "View", key=f"open_{app_id}", use_container_width=True,
                             type="primary" if mine else "secondary"):
                    _open(app_id)
            elif c4.button("Take over" if app["status"] == "under_review" else "Start review",
                           key=f"claim_{app_id}", use_container_width=True):
                r = nav.api("POST", f"/officer/{app_id}/claim")
                if r is not None and r.status_code == 200:
                    _open(app_id)
                elif r is not None:
                    st.error(nav.detail(r, "Could not start this review."))

    prev_col, _, next_col = st.columns([1, 4, 1])
    if offset > 0 and prev_col.button("← Previous"):
        st.session_state["queue_offset"] = max(0, offset - PAGE_SIZE)
        st.rerun()
    if len(apps) == PAGE_SIZE and next_col.button("Next →"):
        st.session_state["queue_offset"] = offset + PAGE_SIZE
        st.rerun()
