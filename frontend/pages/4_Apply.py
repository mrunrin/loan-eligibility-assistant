from datetime import date

import nav
import streamlit as st

PAGE = "pages/4_Apply.py"
BUSINESS_TYPES = ["Sole Proprietorship", "Partnership", "Private Limited", "LLP", "Other"]
EMPLOYMENT = {"salaried": "Salaried", "self_employed": "Self-employed"}
_STATE_KEYS = ("apply_product", "apply_step", "apply_data", "apply_app_id", "apply_files", "apply_uploaded")

# Eligibility hints mirror app/prequal.py; the backend is the source of truth.
PRODUCTS = {
    "business_loan": {
        "label": "Business Loan",
        "blurb": "Working capital, equipment or expansion for your business.",
        "hint": "₹1 lakh – ₹5 crore · 2+ years in business",
        "documents": ["Photo ID", "Bank Statement", "Business Registration Certificate", "GST Certificate", "ITR", "Other"],
    },
    "personal_loan": {
        "label": "Personal Loan",
        "blurb": "Unsecured loan for salaried individuals.",
        "hint": "₹25,000 – ₹50 lakh · age 21–60 · salaried · ₹25,000+ monthly income",
        "documents": ["Photo ID", "Salary Slip", "Bank Statement", "ITR", "Other"],
    },
    "credit_card": {
        "label": "Credit Card",
        "blurb": "Credit limit is set by the lender from your income.",
        "hint": "Age 18+ · salaried or self-employed · ₹25,000+ monthly income",
        "documents": ["Photo ID", "Salary Slip", "Bank Statement", "ITR", "Other"],
    },
}


def _money(v) -> str:
    return f"₹{v:,.0f}" if v is not None else "—"


# (label, key, formatter) shown on the review step, per product
REVIEW = {
    "business_loan": [
        ("Business Name", "business_name", str), ("Business Type", "business_type", str),
        ("Years in Business", "years_in_business", str), ("Loan Purpose", "loan_purpose", str),
        ("Loan Amount", "loan_amount_requested", _money), ("Annual Revenue", "annual_revenue", _money),
        ("Monthly EMI Capacity", "monthly_emi_capacity", _money), ("Credit Score", "credit_score", str),
    ],
    "personal_loan": [
        ("Employment", "employment_type", EMPLOYMENT.get), ("Employer", "employer_name", str),
        ("Date of Birth", "date_of_birth", str), ("Total Work Experience (yrs)", "work_experience_years", str),
        ("Years with Current Employer", "current_job_years", str), ("Loan Purpose", "loan_purpose", str),
        ("Loan Amount", "loan_amount_requested", _money), ("Net Monthly Income", "monthly_income", _money),
        ("Existing Monthly EMIs", "existing_emis", _money), ("Credit Score", "credit_score", str),
    ],
    "credit_card": [
        ("Employment", "employment_type", EMPLOYMENT.get), ("Employer / Business", "employer_name", lambda v: v or "—"),
        ("Date of Birth", "date_of_birth", str), ("Net Monthly Income", "monthly_income", _money),
        ("Existing Monthly EMIs", "existing_emis", _money), ("Credit Score", "credit_score", str),
    ],
}

st.set_page_config(page_title="Apply — Loan Portal", layout="centered")
nav.require_role("borrower")
nav.render_sidebar(current_page=PAGE)


def _reset_apply_state() -> None:
    for key in list(st.session_state.keys()):
        if key in _STATE_KEYS or str(key).startswith("doctype_"):
            del st.session_state[key]


# Submission result is shown on the run *after* submit, so its button is not nested in another button.
if result := st.session_state.get("apply_result"):
    st.title("Application Submitted")
    st.success(
        f"Pre-qualification score: **{result['score']}/100** — "
        f"{result['decision'].replace('_', ' ').upper()}"
    )
    if st.button("View My Applications", type="primary"):
        del st.session_state["apply_result"]
        st.switch_page("pages/3_My_Applications.py")
    st.stop()

# ---------------------------------------------------------------------------
# Step 0 — Choose a product
# ---------------------------------------------------------------------------
product = st.session_state.get("apply_product")
if product is None:
    st.title("What would you like to apply for?")
    for key, info in PRODUCTS.items():
        with st.container(border=True):
            c1, c2 = st.columns([4, 1], vertical_alignment="center")
            c1.markdown(f"**{info['label']}**  \n{info['blurb']}")
            c1.caption(info["hint"])
            if c2.button("Apply", key=f"pick_{key}", use_container_width=True):
                _reset_apply_state()
                st.session_state["apply_product"] = key
                st.rerun()
    st.stop()

st.session_state.setdefault("apply_step", 1)
st.session_state.setdefault("apply_data", {"product_type": product})
st.session_state.setdefault("apply_app_id", None)
st.session_state.setdefault("apply_files", {})      # name → (bytes, mime) kept across reruns
st.session_state.setdefault("apply_uploaded", [])   # names already sent to the backend

step = st.session_state["apply_step"]
data = st.session_state["apply_data"]
info = PRODUCTS[product]

st.title(f"{info['label']} Application")
st.progress(step / 4, text=f"Step {step} of 4")


def _go(to: int) -> None:
    st.session_state["apply_step"] = to
    st.rerun()


def _save_draft() -> bool:
    """Create the draft on first save, then keep it in sync. Returns True on success."""
    app_id = st.session_state["apply_app_id"]
    if app_id:
        resp = nav.api("PATCH", f"/applications/{app_id}", json=data)
        ok = resp is not None and resp.status_code == 200
    else:
        resp = nav.api("POST", "/applications", json=data)
        ok = resp is not None and resp.status_code == 201
        if ok:
            st.session_state["apply_app_id"] = resp.json()["id"]
    if not ok and resp is not None:
        st.error(nav.detail(resp, "Could not save the application draft. Please try again."))
    return ok


def _employment_inputs(employer_label: str) -> tuple[str, str, date]:
    keys = list(EMPLOYMENT)
    employment = st.selectbox(
        "Employment Type", keys, format_func=EMPLOYMENT.get,
        index=keys.index(data.get("employment_type", "salaried")),
    )
    employer = st.text_input(employer_label, value=data.get("employer_name") or "", max_chars=255)
    today = date.today()
    dob = st.date_input(
        "Date of Birth",
        value=date.fromisoformat(data["date_of_birth"]) if data.get("date_of_birth") else date(today.year - 30, 1, 1),
        min_value=date(today.year - 100, 1, 1), max_value=today, format="DD/MM/YYYY",
    )
    return employment, employer.strip(), dob


def _money_input(label: str, key: str, step_size: float, help_text: str | None = None) -> float:
    return st.number_input(label, min_value=0.0, step=step_size, value=float(data.get(key) or 0.0), help=help_text)


# ---------------------------------------------------------------------------
# Step 1 — About you / your business
# ---------------------------------------------------------------------------
if step == 1:
    if product == "business_loan":
        st.subheader("Step 1: Business Information")
        with st.form("step1"):
            business_name = st.text_input("Business Name", value=data.get("business_name", ""), max_chars=255)
            business_type = st.selectbox(
                "Business Type", BUSINESS_TYPES,
                index=BUSINESS_TYPES.index(data.get("business_type", BUSINESS_TYPES[0])),
            )
            years = st.number_input("Years in Business", min_value=0, max_value=100, value=int(data.get("years_in_business", 0)))
            loan_purpose = st.text_input("Loan Purpose", value=data.get("loan_purpose", ""), max_chars=255)
            next_btn = st.form_submit_button("Next →")
        if next_btn:
            if not business_name.strip() or not loan_purpose.strip():
                st.error("Business Name and Loan Purpose are required.")
            else:
                data.update({
                    "business_name": business_name.strip(), "business_type": business_type,
                    "years_in_business": int(years), "loan_purpose": loan_purpose.strip(),
                })
                _go(2)

    elif product == "personal_loan":
        st.subheader("Step 1: Personal & Employment Details")
        with st.form("step1"):
            employment, employer, dob = _employment_inputs("Employer Name")
            c1, c2 = st.columns(2)
            total_exp = c1.number_input("Total Work Experience (years)", min_value=0, max_value=60,
                                        value=int(data.get("work_experience_years", 0)))
            current = c2.number_input("Years with Current Employer", min_value=0, max_value=60,
                                      value=int(data.get("current_job_years", 0)))
            loan_purpose = st.text_input("Loan Purpose", value=data.get("loan_purpose", ""), max_chars=255,
                                         placeholder="e.g. wedding, medical, home renovation")
            next_btn = st.form_submit_button("Next →")
        if next_btn:
            if not employer or not loan_purpose.strip():
                st.error("Employer Name and Loan Purpose are required.")
            elif current > total_exp:
                st.error("Years with current employer cannot exceed total work experience.")
            else:
                data.update({
                    "employment_type": employment, "employer_name": employer, "date_of_birth": dob.isoformat(),
                    "work_experience_years": int(total_exp), "current_job_years": int(current),
                    "loan_purpose": loan_purpose.strip(),
                })
                _go(2)

    else:  # credit_card
        st.subheader("Step 1: Personal & Employment Details")
        with st.form("step1"):
            employment, employer, dob = _employment_inputs("Employer / Business Name (optional)")
            next_btn = st.form_submit_button("Next →")
        if next_btn:
            data.update({"employment_type": employment, "employer_name": employer or None, "date_of_birth": dob.isoformat()})
            _go(2)

    if st.session_state["apply_app_id"] is None and st.button("← Choose a different product"):
        _reset_apply_state()
        st.rerun()

# ---------------------------------------------------------------------------
# Step 2 — Financial details (creates or updates the draft on Next)
# ---------------------------------------------------------------------------
elif step == 2:
    st.subheader("Step 2: Financial Details")
    with st.form("step2"):
        if product == "business_loan":
            fields = {
                "loan_amount_requested": _money_input("Loan Amount Requested (₹)", "loan_amount_requested", 100000.0),
                "annual_revenue": _money_input("Annual Revenue (₹)", "annual_revenue", 100000.0),
                "monthly_emi_capacity": _money_input("Monthly EMI Capacity (₹)", "monthly_emi_capacity", 5000.0),
            }
            required = ("loan_amount_requested", "annual_revenue")
        else:
            fields = {}
            if product == "personal_loan":
                fields["loan_amount_requested"] = _money_input("Loan Amount Requested (₹)", "loan_amount_requested", 25000.0)
            fields["monthly_income"] = _money_input(
                "Net Monthly Income (₹)", "monthly_income", 5000.0, "Take-home income after tax and deductions."
            )
            fields["existing_emis"] = _money_input(
                "Existing Monthly EMIs (₹)", "existing_emis", 1000.0,
                "Total of all EMIs you already pay (home, car, other loans). Enter 0 if none.",
            )
            required = ("loan_amount_requested", "monthly_income") if product == "personal_loan" else ("monthly_income",)
        credit_score = st.number_input("Credit Score (300–900)", min_value=300, max_value=900,
                                       value=int(data.get("credit_score", 650)))
        c1, c2 = st.columns(2)
        back = c1.form_submit_button("← Back")
        nxt = c2.form_submit_button("Next →")

    if back:
        _go(1)
    if nxt:
        if any(fields[k] <= 0 for k in required):
            st.error("Please enter a positive " + " and ".join(k.replace("_", " ") for k in required) + ".")
        else:
            data.update({k: round(v, 2) for k, v in fields.items()})
            data["credit_score"] = int(credit_score)
            if _save_draft():
                _go(3)

# ---------------------------------------------------------------------------
# Step 3 — Document Upload
# ---------------------------------------------------------------------------
elif step == 3:
    st.subheader("Step 3: Upload Documents")
    st.caption("Accepted formats: PDF, JPG, PNG — max 10 MB each.")
    app_id = st.session_state["apply_app_id"]
    files = st.session_state["apply_files"]
    uploaded_names = st.session_state["apply_uploaded"]

    draft = nav.json_or_none(nav.api("GET", f"/applications/{app_id}")) or {}
    have = {d.get("document_type") for d in nav.json_or_none(nav.api("GET", f"/applications/{app_id}/documents")) or []}
    if draft.get("required_documents"):
        st.markdown("**Required before you can submit:**")
        for name in draft["required_documents"]:
            st.write(f"{'✅' if name in have else '⬜'} {name}")

    picked = st.file_uploader("Upload supporting documents", type=["pdf", "jpg", "jpeg", "png"], accept_multiple_files=True)
    for f in picked or []:
        if f.name not in files:
            files[f.name] = (f.getvalue(), f.type)
    # Drop cached bytes for files the user removed from the picker.
    current = {f.name for f in picked or []}
    for name in list(files):
        if name not in current and name not in uploaded_names:
            del files[name]

    pending = [name for name in files if name not in uploaded_names]
    for name in pending:
        st.selectbox(f"Document type for **{name}**", info["documents"], key=f"doctype_{name}")
    if pending and st.button("Upload All"):
        for name in pending:
            file_bytes, mime = files[name]
            resp = nav.api(
                "POST", f"/applications/{app_id}/documents",
                params={"document_type": st.session_state.get(f"doctype_{name}", "Other")},
                files={"file": (name, file_bytes, mime)},
                timeout=60,
            )
            if resp is not None and resp.status_code == 201:
                uploaded_names.append(name)
                files[name] = (b"", mime)  # bytes no longer needed once stored server-side
                st.success(f"Uploaded: {name}")
            elif resp is not None:
                st.error(f"Failed to upload {name}: {nav.detail(resp)}")
    if uploaded_names:
        st.caption("Uploaded: " + ", ".join(uploaded_names))

    c1, c2 = st.columns(2)
    if c1.button("← Back"):
        _go(2)
    if c2.button("Next →"):
        _go(4)

# ---------------------------------------------------------------------------
# Step 4 — Review & Submit
# ---------------------------------------------------------------------------
elif step == 4:
    st.subheader("Step 4: Review & Submit")
    rows = REVIEW[product]
    st.table({
        "Field": [label for label, _, _ in rows],
        "Value": [fmt(data[key]) if data.get(key) is not None else "—" for _, key, fmt in rows],
    })
    if st.session_state["apply_uploaded"]:
        st.write("**Documents:** " + ", ".join(st.session_state["apply_uploaded"]))

    c1, c2 = st.columns(2)
    if c1.button("← Back"):
        _go(3)

    if c2.button("Submit Application", type="primary"):
        app_id = st.session_state.get("apply_app_id")
        if not app_id:
            st.error("Application draft is missing. Go back to step 2.")
            st.stop()
        if not _save_draft():
            st.stop()
        sub = nav.api("POST", f"/applications/{app_id}/submit")
        if sub is not None and sub.status_code == 200:
            _reset_apply_state()
            st.session_state["apply_result"] = sub.json()
            st.rerun()
        elif sub is not None:
            st.error(f"Submission failed: {nav.detail(sub)}")
