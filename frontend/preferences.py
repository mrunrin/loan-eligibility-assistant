"""Optional product preferences on the Apply form (mirrors ApplicationPreferences in app/routers/applications.py).

Each field maps to a policy section the AI Loan Assistant can explain; the help text suggests that question.
None of them change the pre-qualification score.
"""

ASK = "💬 Ask the AI Loan Assistant: "

CHOICES = {
    "employer_category": {
        "public_sector": "Government / PSU", "private_limited": "Private Limited company",
        "public_limited": "Public Limited company", "llp": "LLP", "other": "Other",
    },
    "facility_type": {"term_loan": "Term loan", "dropline_od": "Dropline Overdraft (DOD)"},
    "industry": {
        "trading": "Trading", "manufacturing": "Manufacturing", "services": "Services",
        "professional": "Professional (doctor, CA, architect...)", "other": "Other",
    },
    "card_variant": {
        "entry": "Entry-level (everyday spends)", "cashback": "Shopping / cashback", "premium": "Premium (travel & lounges)",
    },
}

# (key, label) shown on the review step and the application page, per product
FIELDS = {
    "personal_loan": [("employer_category", "Employer Category"), ("credit_protect", "Credit Protect Insurance")],
    "business_loan": [
        ("facility_type", "Facility Type"), ("industry", "Industry"), ("tenure_months", "Tenure (months)"),
        ("balance_transfer", "Balance Transfer"), ("collateral_offered", "Collateral / Guarantor Offered"),
    ],
    "credit_card": [("card_variant", "Card Type"), ("add_on_card", "Add-on Card")],
}

# Final-step acknowledgement: (checkbox label, question it leads to)
TERMS = {
    "credit_card": (
        "I have read the Most Important Terms and Conditions (MITC) and the Cardmember Agreement.",
        "What does the MITC say about interest and late-payment fees?",
    ),
    "loan": (
        "I have read the loan agreement terms, including charges, late payment and default.",
        "What happens if I miss an EMI?",
    ),
}


def display(key: str, value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return CHOICES.get(key, {}).get(value, str(value))


def rows(product: str, prefs: dict | None) -> list[tuple[str, str]]:
    prefs = prefs or {}
    return [(label, display(key, prefs.get(key))) for key, label in FIELDS.get(product, [])]
