import nav
import streamlit as st

PAGE = "pages/5_Application_Detail.py"

st.set_page_config(page_title="Application Detail — Loan Portal", layout="wide")
user = nav.require_login()
nav.render_sidebar(current_page=PAGE)

app_id = st.session_state.get("selected_app_id")
if not app_id:
    st.error("No application selected.")
    st.stop()

resp = nav.api("GET", f"/applications/{app_id}")
app = nav.json_or_none(resp)
if app is None:
    if resp is not None:
        st.error(nav.detail(resp, "Could not load application."))
    st.stop()


def _money(value) -> str:
    return f"₹{value:,.0f}" if value is not None else "—"


def _text(value) -> str:
    return str(value) if value not in (None, "") else "—"


EMPLOYMENT = {"salaried": "Salaried", "self_employed": "Self-employed"}
product = app.get("product_type", "business_loan")
if product == "business_loan":
    left_title, left = "Business Details", [
        ("Business Name", _text(app.get("business_name"))),
        ("Business Type", _text(app.get("business_type"))),
        ("Years in Business", _text(app.get("years_in_business"))),
        ("Loan Purpose", _text(app.get("loan_purpose"))),
    ]
    right = [
        ("Loan Amount", _money(app.get("loan_amount_requested"))),
        ("Annual Revenue", _money(app.get("annual_revenue"))),
        ("Monthly EMI Capacity", _money(app.get("monthly_emi_capacity"))),
    ]
else:
    left_title, left = "Applicant Details", [
        ("Employment", EMPLOYMENT.get(app.get("employment_type"), "—")),
        ("Employer", _text(app.get("employer_name"))),
        ("Date of Birth", _text(app.get("date_of_birth"))),
    ]
    right = [("Net Monthly Income", _money(app.get("monthly_income"))), ("Existing Monthly EMIs", _money(app.get("existing_emis")))]
    if product == "personal_loan":
        left += [
            ("Total Work Experience", f"{_text(app.get('work_experience_years'))} yrs"),
            ("With Current Employer", f"{_text(app.get('current_job_years'))} yrs"),
            ("Loan Purpose", _text(app.get("loan_purpose"))),
        ]
        right.insert(0, ("Loan Amount", _money(app.get("loan_amount_requested"))))
right.append(("Credit Score", _text(app.get("credit_score"))))

st.title(app.get("title") or f"Application {app_id[:8]}")
st.markdown(f"**Status:** `{app['status'].replace('_', ' ').upper()}`")

col1, col2 = st.columns(2)
for column, heading, items in ((col1, left_title, left), (col2, "Financial Details", right)):
    with column:
        st.subheader(heading)
        for label, value in items:
            st.write(f"- **{label}:** {value}")

st.subheader("Pre-Qualification Result")
pq = nav.json_or_none(nav.api("GET", f"/prequal/{app_id}"))
if pq:
    score, decision = pq["score"], pq["decision"]
    color = "green" if decision == "pass" else ("orange" if decision == "manual_review" else "red")
    st.markdown(f"**Score:** {score}/100 — :{color}[{decision.upper().replace('_', ' ')}]")
    st.progress(min(max(score, 0), 100) / 100)
    if pq.get("rule_breakdown"):
        with st.expander("Rule Breakdown"):
            for rule in pq["rule_breakdown"]:
                icon = "✅" if rule["passed"] else "❌"
                tag = " · *eligibility requirement*" if rule.get("knockout") else ""
                tag += " · *self-reported, unverified*" if rule.get("unverified") else ""
                st.write(f"{icon} **{rule['rule']}** (+{rule['score']} pts{tag}) — {rule['message']}")
else:
    st.info("Pre-qualification not run yet.")

st.subheader("Documents")
docs = nav.json_or_none(nav.api("GET", f"/applications/{app_id}/documents"))
if docs is None:
    st.warning("Could not load documents.")
elif not docs:
    st.info("No documents uploaded.")
else:
    for d in docs:
        c1, c2 = st.columns([4, 1])
        c1.write(f"📄 {d['file_name']} ({d.get('document_type') or 'Other'})")
        key = f"doc_bytes_{d['id']}"
        if key in st.session_state:
            c2.download_button("Save", data=st.session_state[key], file_name=d["file_name"], key=f"save_{d['id']}")
            if d["file_name"].lower().endswith((".jpg", ".jpeg", ".png")):
                st.image(st.session_state[key], caption=d["file_name"], width=480)
            elif d["file_name"].lower().endswith(".pdf"):
                st.pdf(st.session_state[key], height=600)
        elif c2.button("Open", key=f"dl_{d['id']}"):
            file_resp = nav.api("GET", f"/applications/{app_id}/documents/{d['id']}/download", timeout=60)
            if file_resp is not None and file_resp.status_code == 200:
                st.session_state[key] = file_resp.content
                st.rerun()
            elif file_resp is not None:
                st.error(nav.detail(file_resp, "Could not download the document."))

# ── Officer decision ─────────────────────────────────────────────────────────
# An officer can only load an under_review application they claimed (the API returns 403 otherwise),
# so reaching this point with that status means the review is theirs (or the viewer is an admin).
if user.get("role") in ("officer", "admin") and app["status"] == "under_review":
    st.divider()
    st.subheader("Your Decision")
    with st.form("decision_form"):
        notes = st.text_area(
            "Reason for decision (required)", max_chars=5000,
            placeholder="e.g. Salary slips and bank statements match the declared income. "
                        "Recorded in the audit log with your name; the borrower and the AI assistant see it.",
        )
        col_a, col_r = st.columns(2)
        approve = col_a.form_submit_button("✅ Approve", type="primary", use_container_width=True)
        reject = col_r.form_submit_button("❌ Reject", use_container_width=True)
    if approve or reject:
        decision = "approved" if approve else "rejected"
        if len(notes.strip()) < 10:
            st.error("Write a reason (at least 10 characters) before approving or rejecting.")
        else:
            r = nav.api("POST", f"/officer/{app_id}/decision", json={"decision": decision, "notes": notes.strip()})
            if r is not None and r.status_code == 200:
                st.session_state["flash_queue"] = f"{app.get('title') or 'Application'} {decision}."
                st.switch_page("pages/6_Officer_Queue.py")
            elif r is not None:
                st.error(nav.detail(r, "Decision failed."))
elif app["status"] in ("approved", "rejected"):
    st.divider()
    st.subheader("Decision")
    icon = "✅ Approved" if app["status"] == "approved" else "❌ Rejected"
    st.markdown(f"**{icon}** on {(app.get('reviewed_at') or '')[:10] or '—'}")
    st.write(f"**Reason:** {app.get('review_notes') or '— (decided before reasons were mandatory)'}")

if app.get("appeal_reason") and user.get("role") in ("officer", "admin"):
    st.divider()
    st.subheader("Borrower's Appeal")
    st.info(app["appeal_reason"])

is_owner = user.get("role") == "borrower"
if is_owner and app["status"] == "pre_qual_fail":
    st.divider()
    st.subheader("Appeal this result")
    st.caption("The reasons are listed under Rule Breakdown above. Explain what they miss, and a loan officer "
               "will review your application manually.")
    with st.form("appeal_form"):
        reason = st.text_area("Your reason (at least 20 characters)", max_chars=2000)
        if st.form_submit_button("Send appeal", type="primary"):
            r = nav.api("POST", f"/applications/{app_id}/appeal", json={"reason": reason.strip()})
            if r is not None and r.status_code == 200:
                st.success("Appeal sent. A loan officer will review your application.")
                st.rerun()
            elif r is not None:
                st.error(nav.detail(r, "Could not send the appeal."))

if is_owner and app["status"] not in ("approved", "rejected", "withdrawn"):
    with st.expander("Withdraw this application"):
        st.caption("The record is kept, but no one will review it any further. This cannot be undone.")
        if st.button("Withdraw application", key="withdraw"):
            r = nav.api("POST", f"/applications/{app_id}/withdraw")
            if r is not None and r.status_code == 200:
                st.rerun()
            elif r is not None:
                st.error(nav.detail(r, "Could not withdraw."))

st.divider()
if st.button("← Back"):
    nav.redirect_to_dashboard()
