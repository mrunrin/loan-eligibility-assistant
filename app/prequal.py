"""
Rule-based pre-qualification engine.

Deterministic and auditable — each rule produces an explicit pass/fail with
a score contribution. This satisfies regulatory explainability requirements
(ECOA, Fair Credit Reporting Act equivalent) that would block an LLM-based
decision system.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.models import LoanApplication


class MissingFieldsError(ValueError):
    def __init__(self, fields: list[str]):
        super().__init__(f"Missing fields: {fields}")
        self.fields = fields


@dataclass
class RuleResult:
    rule: str
    passed: bool
    score_contribution: int
    message: str
    # A failed knockout rule is a hard eligibility criterion: the decision is 'fail' whatever the score.
    knockout: bool = False
    # Rule ran on applicant-declared data (no bureau pull / document check yet); the officer must verify it.
    unverified: bool = False


@dataclass
class PreQualResult:
    score: int
    decision: str  # 'pass' | 'fail' | 'manual_review'
    rule_breakdown: list[dict]


# ---------------------------------------------------------------------------
# Required fields per product
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {
    "business_loan": [
        "business_name",
        "loan_amount_requested",
        "credit_score",
        "annual_revenue",
        "monthly_emi_capacity",
        "years_in_business",
    ],
    "personal_loan": [
        "loan_amount_requested",
        "employment_type",
        "employer_name",
        "date_of_birth",
        "work_experience_years",
        "current_job_years",
        "monthly_income",
        "existing_emis",
        "credit_score",
    ],
    "credit_card": [
        "employment_type",
        "date_of_birth",
        "monthly_income",
        "existing_emis",
        "credit_score",
    ],
}

# ---------------------------------------------------------------------------
# Business loan rules (demo thresholds, unchanged)
# ---------------------------------------------------------------------------

MIN_LOAN = Decimal("100000")       # ₹1,00,000
MAX_LOAN = Decimal("50000000")     # ₹5,00,00,000
MIN_CREDIT_SCORE = 650
CREDIT_SCORE_FLOOR = 550           # below this the application fails outright, whatever the other rules score
MIN_YEARS = 2
REVENUE_MULTIPLE = Decimal("2")    # annual_revenue ≥ 2× requested
ANNUAL_RATE = Decimal("0.12")
LOAN_TERM_MONTHS = 36
_PAISA = Decimal("0.01")


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(_PAISA, rounding=ROUND_HALF_UP)


def emi(principal: Decimal, annual_rate: Decimal, months: int) -> Decimal:
    """EMI = P × r × (1+r)^n / ((1+r)^n - 1), rounded to the paisa. annual_rate is a fraction (0.12)."""
    if principal <= 0 or months <= 0:
        return Decimal("0.00")
    r = annual_rate / 12
    if r == 0:
        return (principal / months).quantize(_PAISA, rounding=ROUND_HALF_UP)
    growth = (1 + r) ** months
    return (principal * r * growth / (growth - 1)).quantize(_PAISA, rounding=ROUND_HALF_UP)


def estimated_emi(principal: Decimal) -> Decimal:
    """EMI used by the affordability rule: 12% p.a. over 36 months."""
    return emi(principal, ANNUAL_RATE, LOAN_TERM_MONTHS)


def _age(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _rule_min_loan(app: LoanApplication, _today: date) -> RuleResult:
    amount = _money(app.loan_amount_requested)
    passed = amount >= MIN_LOAN
    return RuleResult(
        rule="Minimum loan amount",
        passed=passed,
        score_contribution=5 if passed else 0,
        knockout=True,
        message=f"Requested ₹{amount:,.0f} ({'≥' if passed else '<'} minimum ₹{MIN_LOAN:,.0f})",
    )


def _rule_max_loan(app: LoanApplication, _today: date) -> RuleResult:
    amount = _money(app.loan_amount_requested)
    passed = amount <= MAX_LOAN
    return RuleResult(
        rule="Maximum loan amount",
        passed=passed,
        score_contribution=5 if passed else 0,
        knockout=True,
        message=f"Requested ₹{amount:,.0f} ({'≤' if passed else '>'} maximum ₹{MAX_LOAN:,.0f})",
    )


def _rule_business_vintage(app: LoanApplication, _today: date) -> RuleResult:
    years = int(app.years_in_business)
    passed = years >= MIN_YEARS
    return RuleResult(
        rule="Business vintage",
        passed=passed,
        score_contribution=20 if passed else 0,
        message=f"{years} year(s) in business ({'≥' if passed else '<'} required {MIN_YEARS})",
    )


def _rule_revenue_ratio(app: LoanApplication, _today: date) -> RuleResult:
    revenue = _money(app.annual_revenue)
    required = REVENUE_MULTIPLE * _money(app.loan_amount_requested)
    passed = revenue >= required
    return RuleResult(
        rule="Revenue-to-loan ratio",
        passed=passed,
        score_contribution=20 if passed else 0,
        message=(
            f"Annual revenue ₹{revenue:,.0f} "
            f"({'≥' if passed else '<'} required ₹{required:,.0f} = {REVENUE_MULTIPLE}× loan)"
        ),
        unverified=True,
    )


def _rule_emi_affordability(app: LoanApplication, _today: date) -> RuleResult:
    capacity = _money(app.monthly_emi_capacity)
    emi = estimated_emi(_money(app.loan_amount_requested))
    passed = capacity >= emi
    return RuleResult(
        rule="EMI affordability",
        passed=passed,
        score_contribution=25 if passed else 0,
        message=(
            f"Monthly EMI capacity ₹{capacity:,.2f} "
            f"({'≥' if passed else '<'} estimated EMI ₹{emi:,.2f})"
        ),
        unverified=True,
    )


# ---------------------------------------------------------------------------
# Personal loan and credit card rules
#
# Knockout thresholds come from HDFC Bank's public eligibility pages in data/
# (hdfc_personal_loan_eligibility.md, hdfc_credit_card_eligibility.md).
# Credit score and FOIR are portal policy: those pages state no universal CIBIL cutoff.
# Weights: knockouts 30, credit score 35, FOIR 35 — so one scored failure lands in
# manual review (65) and two land in fail (30).
# ---------------------------------------------------------------------------

PL_MIN_LOAN = Decimal("25000")       # HDFC PL page: ₹25,000 – ₹50 lakh
PL_MAX_LOAN = Decimal("5000000")
PL_MIN_AGE, PL_MAX_AGE = 21, 60      # HDFC PL page
PL_MIN_EXPERIENCE_YEARS = 2          # HDFC PL page: 2 years total ...
PL_MIN_CURRENT_JOB_YEARS = 1         # ... including 1 year with the current employer
MIN_MONTHLY_INCOME = Decimal("25000")  # HDFC PL page; reused as this portal's entry-level card threshold
CC_MIN_AGE = 18                      # HDFC credit-card overview
MAX_FOIR = Decimal("0.50")           # portal policy: all EMIs ≤ 50% of net monthly income


def _rule_pl_amount(app: LoanApplication, _today: date) -> RuleResult:
    amount = _money(app.loan_amount_requested)
    passed = PL_MIN_LOAN <= amount <= PL_MAX_LOAN
    return RuleResult(
        rule="Loan amount range",
        passed=passed,
        score_contribution=5 if passed else 0,
        message=f"Requested ₹{amount:,.0f} (allowed ₹{PL_MIN_LOAN:,.0f} – ₹{PL_MAX_LOAN:,.0f})",
        knockout=True,
    )


def _age_rule(min_age: int, max_age: int | None, points: int):
    def rule(app: LoanApplication, today: date) -> RuleResult:
        age = _age(app.date_of_birth, today)
        passed = age >= min_age and (max_age is None or age <= max_age)
        allowed = f"{min_age}–{max_age}" if max_age else f"{min_age}+"
        return RuleResult(
            rule="Applicant age",
            passed=passed,
            score_contribution=points if passed else 0,
            message=f"Age {age} (allowed {allowed})",
            knockout=True,
        )
    return rule


def _rule_pl_salaried(app: LoanApplication, _today: date) -> RuleResult:
    passed = app.employment_type == "salaried"
    return RuleResult(
        rule="Employment type",
        passed=passed,
        score_contribution=5 if passed else 0,
        message=(
            "Salaried" if passed
            else "Self-employed — this personal loan is offered to salaried applicants only"
        ),
        knockout=True,
    )


def _rule_pl_experience(app: LoanApplication, _today: date) -> RuleResult:
    total, current = int(app.work_experience_years), int(app.current_job_years)
    passed = total >= PL_MIN_EXPERIENCE_YEARS and current >= PL_MIN_CURRENT_JOB_YEARS
    return RuleResult(
        rule="Work experience",
        passed=passed,
        score_contribution=5 if passed else 0,
        message=(
            f"{total} year(s) total, {current} with current employer "
            f"(required {PL_MIN_EXPERIENCE_YEARS} total, {PL_MIN_CURRENT_JOB_YEARS} current)"
        ),
        knockout=True,
    )


def _income_rule(points: int):
    def rule(app: LoanApplication, _today: date) -> RuleResult:
        income = _money(app.monthly_income)
        passed = income >= MIN_MONTHLY_INCOME
        return RuleResult(
            rule="Minimum monthly income",
            passed=passed,
            score_contribution=points if passed else 0,
            message=f"Net monthly income ₹{income:,.0f} ({'≥' if passed else '<'} required ₹{MIN_MONTHLY_INCOME:,.0f})",
            knockout=True,
            unverified=True,
        )
    return rule


def _credit_score_rule(points: int):
    def rule(app: LoanApplication, _today: date) -> RuleResult:
        score = int(app.credit_score)
        passed = score >= MIN_CREDIT_SCORE
        return RuleResult(
            rule="Minimum credit score",
            passed=passed,
            score_contribution=points if passed else 0,
            message=(
                f"Credit score {score} ({'≥' if passed else '<'} required {MIN_CREDIT_SCORE}"
                f"{f'; below hard floor {CREDIT_SCORE_FLOOR}' if score < CREDIT_SCORE_FLOOR else ''})"
            ),
            knockout=score < CREDIT_SCORE_FLOOR,
            unverified=True,
        )
    return rule


def _foir_rule(include_new_emi: bool):
    def rule(app: LoanApplication, _today: date) -> RuleResult:
        income = _money(app.monthly_income)
        existing = _money(app.existing_emis)
        new_emi = estimated_emi(_money(app.loan_amount_requested)) if include_new_emi else Decimal("0")
        obligations = existing + new_emi
        foir = obligations / income if income > 0 else Decimal("1")
        passed = foir <= MAX_FOIR
        detail = f"existing ₹{existing:,.0f} + new EMI ₹{new_emi:,.0f}" if include_new_emi else f"existing EMIs ₹{existing:,.0f}"
        return RuleResult(
            rule="Fixed obligations to income (FOIR)",
            passed=passed,
            score_contribution=35 if passed else 0,
            message=f"{foir:.0%} of net monthly income ({detail}; allowed ≤ {MAX_FOIR:.0%})",
            unverified=True,
        )
    return rule


# ---------------------------------------------------------------------------
# Engine entry point
# ---------------------------------------------------------------------------

_RULES = {
    "business_loan": [
        _rule_min_loan,
        _rule_max_loan,
        _credit_score_rule(25),
        _rule_business_vintage,
        _rule_revenue_ratio,
        _rule_emi_affordability,
    ],
    "personal_loan": [
        _rule_pl_amount,
        _age_rule(PL_MIN_AGE, PL_MAX_AGE, 5),
        _rule_pl_salaried,
        _rule_pl_experience,
        _income_rule(10),
        _credit_score_rule(35),
        _foir_rule(include_new_emi=True),
    ],
    "credit_card": [
        _age_rule(CC_MIN_AGE, None, 10),
        _income_rule(20),
        _credit_score_rule(35),
        _foir_rule(include_new_emi=False),
    ],
}


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def missing_fields(app: LoanApplication) -> list[str]:
    if app.product_type not in _RULES:
        raise ValueError(f"Unknown product type: {app.product_type!r}")
    return [f for f in REQUIRED_FIELDS[app.product_type] if _is_missing(getattr(app, f))]


def failed_reasons(result: PreQualResult) -> list[str]:
    """Plain-language reasons for every failed rule, as sent to the borrower in writing."""
    return [f"{r['rule']}: {r['message']}" for r in result.rule_breakdown if not r["passed"]]


def run_prequal(app: LoanApplication, today: date | None = None) -> PreQualResult:
    product = app.product_type
    missing = missing_fields(app)
    if missing:
        raise MissingFieldsError(missing)
    today = today or date.today()
    results = [r(app, today) for r in _RULES[product]]
    total_score = sum(r.score_contribution for r in results)

    if any(r.knockout and not r.passed for r in results):
        decision = "fail"
    elif total_score >= 70:
        decision = "pass"
    elif total_score >= 50:
        decision = "manual_review"
    else:
        decision = "fail"

    breakdown = [
        {
            "rule": r.rule,
            "passed": r.passed,
            "score": r.score_contribution,
            "message": r.message,
            "knockout": r.knockout,
            "unverified": r.unverified,
        }
        for r in results
    ]

    return PreQualResult(score=total_score, decision=decision, rule_breakdown=breakdown)


_STATUS_FOR_DECISION = {"pass": "pre_qual_pass", "fail": "pre_qual_fail", "manual_review": "manual_review"}


async def apply_prequal(db, app: LoanApplication) -> PreQualResult:
    """Run the engine, move the application to the matching status, and upsert its result row."""
    from datetime import datetime, timezone

    from sqlalchemy.future import select

    from app.models import PreQualificationResult

    result = run_prequal(app)
    app.status = _STATUS_FOR_DECISION[result.decision]
    existing = await db.execute(
        select(PreQualificationResult).where(PreQualificationResult.application_id == app.id)
    )
    record = existing.scalar_one_or_none()
    if record is None:
        db.add(PreQualificationResult(
            application_id=app.id,
            score=result.score,
            decision=result.decision,
            rule_breakdown=result.rule_breakdown,
        ))
    else:
        record.score = result.score
        record.decision = result.decision
        record.rule_breakdown = result.rule_breakdown
        record.ran_at = datetime.now(timezone.utc)
    return result
