# ADR-011 — Rule-Based Pre-Qualification Engine

**Status:** Accepted  
**Date:** 2026-10-21

## Context

The portal needs to automatically assess loan applications before an officer reviews them. The result must be explainable to the applicant and defensible to a regulator. See [ADR-000](000-constraints.md).

## Decision

Use a **deterministic rule engine** in `app/prequal.py`. Each product has its own weighted rules (score 0–100) plus knockout rules; thresholds determine pass / manual_review / fail. The breakdown is stored as JSONB in `pre_qualification_results.rule_breakdown`. The business-loan rules are below; personal-loan and credit-card rules are in [ADR-019](019-multi-product-applications.md).

## Rules (business loan)

| Rule | Pass condition | Weight |
|---|---|---|
| Loan amount minimum | ≥ ₹1,00,000 | 5 (knockout) |
| Loan amount maximum | ≤ ₹5,00,00,000 | 5 (knockout) |
| Credit score | ≥ 650 (below 550 is a knockout) | 25 |
| Business vintage | years_in_business ≥ 2 | 20 |
| Revenue-to-loan ratio | annual_revenue ≥ 2× requested | 20 |
| EMI affordability | monthly_emi_capacity ≥ estimated_monthly_emi | 25 |

A failed knockout rule makes the decision `fail` whatever the score; without it a ₹50 Cr request scored 95 and passed. Otherwise score ≥ 70 → `pass`; 50–69 → `manual_review`; < 50 → `fail`.

Rules that use self-reported figures (credit score, revenue, EMI capacity, income, FOIR) carry `unverified: true` in the breakdown; the officer review is the verification step until a bureau pull exists.

EMI formula: `P × r × (1+r)^n / ((1+r)^n − 1)` where r = 12%/12, n = 36 months.

## Why not an ML model

An ML credit scoring model requires a training dataset, a feature pipeline, model versioning, and monitoring for drift. None of these are available in a capstone context with synthetic data. More importantly, deterministic rules are **auditable** — the applicant can be shown exactly which rule they failed, which is a regulatory requirement under India's Fair Practices Code for Lenders (RBI circular). An LLM making eligibility decisions is a hallucination risk; a rule engine is correct by construction for its domain.

## Why not send the application to the LLM for a decision

An LLM might miscount a credit score, hallucinate a regulatory threshold, or produce different answers to the same input on successive calls. Financial eligibility decisions cannot be non-deterministic.

## Consequences

- Pre-qual runs automatically on `POST /applications/{id}/submit` and is idempotent (a unique constraint on `application_id` in `pre_qualification_results` prevents duplicates).
- Officers can re-run pre-qual on a submitted application if the applicant updates their data.
- Submit is refused until the required fields and the product's required documents are present (422 lists what is missing).
- A `pre_qual_fail` is not a dead end: the borrower is emailed the failed rules in writing (RBI Fair Practices Code) and can appeal with a written reason, which moves the application to `manual_review` for an officer.
- The `rule_breakdown` JSONB is not shown as a raw list any more. Borrowers are sent to the AI assistant ("Ask here"), which reads it. Loan officers see a 5–6 line **AI summary** of it in an "AI generated summary" dropdown (`GET /prequal/{id}/summary`, officers only, `app/case_summary.py`); admins keep the plain rule breakdown: one Groq call over the rule results with amounts banded, cached per pre-qual run, labelled "AI can make mistakes", and falling back to a plain rules summary when Groq is unavailable. The summary explains; it never changes the score or the decision, which stay rule-based.

## Production path

Replace or supplement rules with a trained scorecard model (logistic regression on bureau data), keeping the same `PreQualResult` response contract. An ML model is an enhancement, not a replacement for the explainability requirement — the explanation layer remains rule-based even if the scoring is statistical.
