"""
Unit tests for the pre-qualification rule engine.

Tests boundary values for all 6 rules with pass/fail/manual_review scenarios.
No database required — the engine is pure Python.
"""

from unittest.mock import MagicMock

import pytest

from app.prequal import run_prequal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(**kwargs) -> MagicMock:
    """Build a mock LoanApplication with all required fields defaulted to passing values."""
    defaults = {
        "product_type": "business_loan",
        "business_name": "Test Traders",
        "loan_amount_requested": 1_000_000,   # ₹10L — passes min/max
        "annual_revenue": 3_000_000,           # ₹30L — 3× loan, passes ratio
        "monthly_emi_capacity": 100_000,       # ₹1L — comfortably above EMI
        "credit_score": 700,                   # ≥ 650, passes
        "years_in_business": 3,                # ≥ 2, passes
        "loan_purpose": "expansion",
    }
    defaults.update(kwargs)
    app = MagicMock()
    for k, v in defaults.items():
        setattr(app, k, v)
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_all_rules_pass_gives_pass():
    app = _make_app()
    result = run_prequal(app)
    assert result.decision == "pass"
    assert result.score >= 70


def test_low_credit_score_fails_rule():
    app = _make_app(credit_score=600)  # below 650
    result = run_prequal(app)
    credit_rule = next(r for r in result.rule_breakdown if r["rule"] == "Minimum credit score")
    assert not credit_rule["passed"]
    assert credit_rule["score"] == 0


def test_credit_score_at_boundary_passes():
    app = _make_app(credit_score=650)  # exact threshold
    result = run_prequal(app)
    credit_rule = next(r for r in result.rule_breakdown if r["rule"] == "Minimum credit score")
    assert credit_rule["passed"]
    assert credit_rule["score"] == 25


def test_insufficient_business_vintage():
    app = _make_app(years_in_business=1)  # < 2 years
    result = run_prequal(app)
    vintage_rule = next(r for r in result.rule_breakdown if r["rule"] == "Business vintage")
    assert not vintage_rule["passed"]


def test_business_vintage_at_boundary_passes():
    app = _make_app(years_in_business=2)  # exact threshold
    result = run_prequal(app)
    vintage_rule = next(r for r in result.rule_breakdown if r["rule"] == "Business vintage")
    assert vintage_rule["passed"]


def test_revenue_ratio_fails():
    # Revenue must be ≥ 2× loan; here it's only 1.5×
    app = _make_app(loan_amount_requested=1_000_000, annual_revenue=1_500_000)
    result = run_prequal(app)
    ratio_rule = next(r for r in result.rule_breakdown if r["rule"] == "Revenue-to-loan ratio")
    assert not ratio_rule["passed"]


def test_revenue_ratio_passes_exactly_twice():
    app = _make_app(loan_amount_requested=1_000_000, annual_revenue=2_000_000)
    result = run_prequal(app)
    ratio_rule = next(r for r in result.rule_breakdown if r["rule"] == "Revenue-to-loan ratio")
    assert ratio_rule["passed"]


def test_emi_affordability_fails():
    # 12% pa, 36 months on ₹10L → EMI ≈ ₹33,214; capacity set to ₹30K
    app = _make_app(loan_amount_requested=1_000_000, monthly_emi_capacity=30_000)
    result = run_prequal(app)
    emi_rule = next(r for r in result.rule_breakdown if r["rule"] == "EMI affordability")
    assert not emi_rule["passed"]


def test_emi_affordability_passes():
    # Same loan but capacity ₹40K > estimated ₹33,214
    app = _make_app(loan_amount_requested=1_000_000, monthly_emi_capacity=40_000)
    result = run_prequal(app)
    emi_rule = next(r for r in result.rule_breakdown if r["rule"] == "EMI affordability")
    assert emi_rule["passed"]


def test_loan_below_minimum():
    app = _make_app(loan_amount_requested=50_000)  # < ₹1L
    result = run_prequal(app)
    min_rule = next(r for r in result.rule_breakdown if r["rule"] == "Minimum loan amount")
    assert not min_rule["passed"]


def test_loan_above_maximum():
    app = _make_app(loan_amount_requested=100_000_000)  # > ₹5Cr
    result = run_prequal(app)
    max_rule = next(r for r in result.rule_breakdown if r["rule"] == "Maximum loan amount")
    assert not max_rule["passed"]


def test_manual_review_band():
    # Drop credit_score and vintage to land in 50-69 band
    app = _make_app(credit_score=600, years_in_business=1)
    result = run_prequal(app)
    # Lost 25 (credit) + 20 (vintage) = 45 pts lost, score = 55 → manual_review
    assert result.decision == "manual_review"
    assert 50 <= result.score < 70


def test_fail_decision():
    # Drop credit, vintage, revenue — lose ≥ 50 pts
    app = _make_app(
        credit_score=500,       # fail (-25)
        years_in_business=0,    # fail (-20)
        annual_revenue=100_000, # fail revenue ratio (-20)
    )
    result = run_prequal(app)
    assert result.decision == "fail"
    assert result.score < 50


def test_rule_breakdown_has_all_rules():
    app = _make_app()
    result = run_prequal(app)
    rule_names = {r["rule"] for r in result.rule_breakdown}
    expected = {
        "Minimum loan amount",
        "Maximum loan amount",
        "Minimum credit score",
        "Business vintage",
        "Revenue-to-loan ratio",
        "EMI affordability",
    }
    assert rule_names == expected


def test_emi_exact_boundary_passes_and_one_paisa_below_fails():
    from decimal import Decimal

    from app.prequal import estimated_emi

    emi = estimated_emi(Decimal("1000000"))
    assert emi == Decimal("33214.31")
    at = run_prequal(_make_app(loan_amount_requested=1_000_000, monthly_emi_capacity=emi))
    below = run_prequal(_make_app(loan_amount_requested=1_000_000, monthly_emi_capacity=emi - Decimal("0.01")))
    rule = lambda r: next(x for x in r.rule_breakdown if x["rule"] == "EMI affordability")  # noqa: E731
    assert rule(at)["passed"] and not rule(below)["passed"]


def test_missing_fields_raise_instead_of_scoring_as_zero():
    from app.prequal import MissingFieldsError

    with pytest.raises(MissingFieldsError) as exc:
        run_prequal(_make_app(credit_score=None, monthly_emi_capacity=None))
    assert set(exc.value.fields) == {"credit_score", "monthly_emi_capacity"}


def test_decimal_and_float_inputs_agree():
    from decimal import Decimal

    as_float = run_prequal(_make_app(annual_revenue=2_000_000.0))
    as_decimal = run_prequal(_make_app(annual_revenue=Decimal("2000000.00")))
    assert as_float.score == as_decimal.score


def test_loan_at_maximum_passes_and_above_fails():
    at_max = run_prequal(_make_app(loan_amount_requested=50_000_000))
    above = run_prequal(_make_app(loan_amount_requested=50_000_000.01))
    rule = lambda r: next(x for x in r.rule_breakdown if x["rule"] == "Maximum loan amount")  # noqa: E731
    assert rule(at_max)["passed"] and not rule(above)["passed"]


# ---------------------------------------------------------------------------
# Personal loan and credit card
# ---------------------------------------------------------------------------

from datetime import date  # noqa: E402

TODAY = date(2026, 10, 22)


def _make_individual(product: str, **kwargs) -> MagicMock:
    defaults = {
        "product_type": product,
        "loan_amount_requested": 500_000 if product == "personal_loan" else None,
        "employment_type": "salaried",
        "employer_name": "Infosys",
        "date_of_birth": date(1994, 5, 10),     # 32 on TODAY
        "work_experience_years": 5,
        "current_job_years": 2,
        "monthly_income": 80_000,
        "existing_emis": 5_000,
        "credit_score": 720,
    }
    defaults.update(kwargs)
    app = MagicMock()
    for k, v in defaults.items():
        setattr(app, k, v)
    return app


def _rule(result, name):
    return next(r for r in result.rule_breakdown if r["rule"] == name)


def test_personal_loan_all_rules_pass():
    result = run_prequal(_make_individual("personal_loan"), today=TODAY)
    assert (result.decision, result.score) == ("pass", 100)


def test_personal_loan_self_employed_is_knockout_fail_despite_high_score():
    result = run_prequal(_make_individual("personal_loan", employment_type="self_employed"), today=TODAY)
    assert result.score == 95 and result.decision == "fail"
    assert _rule(result, "Employment type")["knockout"]


def test_personal_loan_age_boundaries():
    at_21 = run_prequal(_make_individual("personal_loan", date_of_birth=date(2005, 10, 22)), today=TODAY)
    day_before_21 = run_prequal(_make_individual("personal_loan", date_of_birth=date(2005, 10, 23)), today=TODAY)
    at_60 = run_prequal(_make_individual("personal_loan", date_of_birth=date(1966, 10, 23)), today=TODAY)
    at_61 = run_prequal(_make_individual("personal_loan", date_of_birth=date(1965, 10, 22)), today=TODAY)
    assert _rule(at_21, "Applicant age")["passed"] and _rule(at_60, "Applicant age")["passed"]
    assert day_before_21.decision == "fail" and at_61.decision == "fail"


def test_personal_loan_experience_needs_both_total_and_current():
    result = run_prequal(_make_individual("personal_loan", work_experience_years=5, current_job_years=0), today=TODAY)
    assert result.decision == "fail" and not _rule(result, "Work experience")["passed"]


def test_personal_loan_amount_and_income_floors():
    assert run_prequal(_make_individual("personal_loan", loan_amount_requested=24_999), today=TODAY).decision == "fail"
    assert run_prequal(_make_individual("personal_loan", monthly_income=24_999), today=TODAY).decision == "fail"
    assert run_prequal(_make_individual("personal_loan", monthly_income=25_000, existing_emis=0,
                                        loan_amount_requested=100_000), today=TODAY).decision == "pass"


def test_personal_loan_foir_includes_new_emi():
    # ₹5L at 12%/36m → EMI ₹16,607; 16,607 + 30,000 existing = 58% of ₹80K → fails FOIR only
    result = run_prequal(_make_individual("personal_loan", existing_emis=30_000), today=TODAY)
    assert not _rule(result, "Fixed obligations to income (FOIR)")["passed"]
    assert (result.decision, result.score) == ("manual_review", 65)


def test_personal_loan_low_credit_and_high_foir_fails():
    result = run_prequal(_make_individual("personal_loan", credit_score=600, existing_emis=30_000), today=TODAY)
    assert (result.decision, result.score) == ("fail", 30)


def test_credit_card_pass_and_age_knockout():
    assert run_prequal(_make_individual("credit_card"), today=TODAY).decision == "pass"
    at_18 = run_prequal(_make_individual("credit_card", date_of_birth=date(2008, 10, 22)), today=TODAY)
    at_17 = run_prequal(_make_individual("credit_card", date_of_birth=date(2008, 10, 23)), today=TODAY)
    assert at_18.decision == "pass" and at_17.decision == "fail"


def test_credit_card_allows_self_employed_and_ignores_loan_amount():
    result = run_prequal(_make_individual("credit_card", employment_type="self_employed"), today=TODAY)
    assert result.decision == "pass"
    assert {r["rule"] for r in result.rule_breakdown} == {
        "Applicant age", "Minimum monthly income", "Minimum credit score", "Fixed obligations to income (FOIR)",
    }


def test_credit_card_low_credit_score_goes_to_manual_review():
    result = run_prequal(_make_individual("credit_card", credit_score=640), today=TODAY)
    assert (result.decision, result.score) == ("manual_review", 65)


def test_missing_fields_are_product_specific():
    from app.prequal import MissingFieldsError

    with pytest.raises(MissingFieldsError) as exc:
        run_prequal(_make_individual("personal_loan", employer_name="  ", date_of_birth=None), today=TODAY)
    assert set(exc.value.fields) == {"employer_name", "date_of_birth"}
    # A credit card needs no loan amount or employer
    run_prequal(_make_individual("credit_card", loan_amount_requested=None, employer_name=None), today=TODAY)


def test_unknown_product_is_rejected():
    with pytest.raises(ValueError):
        run_prequal(_make_individual("home_loan"), today=TODAY)


def test_business_loan_out_of_range_is_knockout_fail_despite_high_score():
    result = run_prequal(_make_app(loan_amount_requested=500_000_000, annual_revenue=2_000_000_000,
                                   monthly_emi_capacity=50_000_000))
    assert result.score == 95 and result.decision == "fail"


def test_credit_score_below_floor_is_knockout_fail():
    result = run_prequal(_make_app(credit_score=549))
    assert result.decision == "fail" and _rule(result, "Minimum credit score")["knockout"]
    assert run_prequal(_make_app(credit_score=550)).decision == "pass"  # 75: floor met, 650 rule lost


def test_self_reported_rules_are_flagged_unverified():
    result = run_prequal(_make_app())
    assert _rule(result, "Minimum credit score")["unverified"]
    assert not _rule(result, "Business vintage")["unverified"]
