# ADR-019 — Personal Loan and Credit Card Applications

**Status:** Accepted
**Date:** 2026-10-22
**Extends:** [ADR-011](011-rule-prequal.md) (rule-based pre-qualification)

## Context

The portal took only one kind of application: a business loan. The knowledge base already answers personal-loan and credit-card questions (HDFC and RBI documents in `data/`), but a borrower could not apply for either product. The two products need different inputs: a personal loan is assessed on salary, age and work history, not on business revenue, and a credit card has no loan amount at all.

## Decision

### Data model: one table with a `product_type` column

`loan_applications` gains a `product_type` enum (`business_loan`, `personal_loan`, `credit_card`) plus nullable columns for individual applicants: `employment_type`, `employer_name`, `date_of_birth`, `work_experience_years`, `current_job_years`, `monthly_income` and `existing_emis`. Migration `009` backfills every existing row as `business_loan`, and it can be reversed.

| Alternative | Why rejected |
|---|---|
| A separate table per product | The officer queue, admin stats, documents, pre-qual results, chat context and audit all key on one `application_id`. Three tables would mean three versions of every one of those queries. |
| One `details` JSONB column per product | Loses column types, CHECK constraints and simple SQL reporting (`WHERE monthly_income < …`). JSONB suits fields that change often, and these fields are fixed by credit policy. |
| Reuse business columns (`annual_revenue` as salary × 12) | Puts two meanings in one column. A reviewer reading the schema could not tell which meaning applies to a given row. |

Which fields are required for each product is defined in one place, `app/prequal.py::REQUIRED_FIELDS`. The two hard-coded `business_name` checks in the routers were removed.

### Pre-qualification: separate rule set per product, plus knockout rules

| Product | Rule | Threshold | Source | Points |
|---|---|---|---|---|
| Personal loan | Loan amount range | ₹25,000 – ₹50 lakh | HDFC PL page | 5 (knockout) |
| | Applicant age | 21–60 | HDFC PL page | 5 (knockout) |
| | Employment type | Salaried only | HDFC PL page | 5 (knockout) |
| | Work experience | 2 years total, 1 year with current employer | HDFC PL page | 5 (knockout) |
| | Minimum monthly income | ₹25,000 net | HDFC PL page | 10 (knockout) |
| | Minimum credit score | 650 | Portal policy | 35 |
| | FOIR (existing + new EMI) / income | ≤ 50% | Portal policy (industry norm) | 35 |
| Credit card | Applicant age | 18+ | HDFC card overview | 10 (knockout) |
| | Minimum monthly income | ₹25,000 net | Portal entry-card threshold | 20 (knockout) |
| | Minimum credit score | 650 | Portal policy | 35 |
| | FOIR (existing EMIs) / income | ≤ 50% | Portal policy | 35 |
| Business loan | See [ADR-011](011-rule-prequal.md) (min/max amount and credit score below 550 are knockouts since 2026-10-23) | | | |

Applies to every product: a credit score below 550 is also a knockout, and rules on self-reported figures (credit score, income, FOIR) are flagged `unverified` until an officer checks them. Submit also requires the product's documents: personal loan — Photo ID, Salary Slip, Bank Statement; credit card — Photo ID, Bank Statement.

- **Knockout rules:** a failed knockout rule makes the decision `fail` whatever the score. Without knockouts, a self-employed applicant would score 95/100 and pass a product they are not eligible for. Seed application APP-2026-019 shows this case.
- **Weights:** the knockout rules together are worth 30 points. If all knockouts pass, one failed credit rule gives a score of 65, which is `manual_review`. Two failed credit rules give 30, which is `fail`. The bands (70 / 50) are the same as for business loans.
- **Portal policy vs bank policy:** credit score and FOIR are labelled portal policy. The HDFC eligibility sources (now merged into `data/loanmitra_personal_loan.md` and `data/loanmitra_credit_card.md`) both state that HDFC publishes **no universal CIBIL cutoff**, so these thresholds are not presented as bank rules.
- **Credit limit:** the credit card application asks for no amount. The HDFC page says the lender sets the limit from income, and inventing a limit formula would be fabricated policy.

### Chat assistant and frontend

- `LoanApplication.title` (for example "Personal Loan · Infosys" or "Credit Card") is now the display name in My Applications, the officer queue and the chat selector. The frontend no longer reads `business_name` to label an application.
- In the assistant's application context, failed knockout rules are marked "hard eligibility requirement". This stops the bot from advising someone to improve something they cannot change, such as their age.
- The Apply page starts with a product picker, then shows a 4-step form for the chosen product. The product can be changed only before the draft is saved.

## Verification

| Check | Result |
|---|---|
| `pytest app/tests` | 65 passed (12 new engine tests, 2 new API tests) |
| `alembic upgrade head` → `downgrade -1` → `upgrade head` | Clean |
| `scripts/seed_demo.py` run twice | 6 PL/CC applications created on the first run, 0 on the second (idempotent) |
| UI: personal loan, ₹5 L, ₹80 K income, ₹30 K existing EMIs | 65/100 manual review; breakdown shows FOIR 58% as the only failure |
| UI: credit card, self-employed, no employer | 100/100 pass |
| Chat: "why is my personal loan stuck in manual review" | Routed to `my_application`; answer explains FOIR 58% vs ≤ 50% |

## Known gaps

1. ~~Business-loan min/max amount rules are still scored rules, not knockouts.~~ Fixed 2026-10-23: a ₹50 Cr request scored 95 and passed, so out-of-range amounts are now knockouts ([ADR-011](011-rule-prequal.md)).
2. Age is calculated on the date of submission. An officer re-running pre-qual later uses the age on that day.
3. The PL FOIR rule assumes 12% over 36 months for the new EMI, the same assumption as business loans. The HDFC PL rack rate is 9.99–24%. The production path is to take the rate and tenure from the offer.
4. No credit-card variants (for example, Regalia has higher thresholds). The production path is a `card_variant` column with a rule set for each variant.
