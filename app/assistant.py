"""
Conversational assistant: query understanding -> tool routing -> grounded answer.

1. policy_qa.precheck  — guardrails, name memory, greetings. No LLM, so PII never reaches any model.
2. route()       — one call to a small model rewrites the turn into a standalone question
                   ("what should i do now" -> "What can I do after my loan application was rejected?")
                   and picks a tool. On any failure it falls back to policy search (the old behaviour).
3. tools         — policy_search: hybrid RAG over the policy corpus (policy_qa.prepare_answer)
                   my_application: the caller's own applications from Postgres, scoped by user id in code
                   emi: deterministic formula; asks for missing inputs instead of guessing
4. policy_qa.generate  — the answer model writes the reply from tool output only, with citations.

Chat history lives in chat_messages so follow-ups survive restarts and multiple workers.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import policy_qa
from app.config import MAX_HISTORY_MESSAGES, ROUTER_MODEL
from app.guardrails_config import check_input
from app.llm import groq_chat
from app.models import PRODUCT_LABELS, ChatMessage, LoanApplication, User
from app.prequal import emi

logger = logging.getLogger(__name__)

INTENTS = ("policy", "my_application", "emi", "small_talk", "out_of_scope")
ROUTER_HISTORY_MESSAGES = 6
ROUTER_MESSAGE_CHARS = 400
MAX_CONTEXT_APPLICATIONS = 3

OUT_OF_SCOPE_ANSWER = (
    "I can only help with loans, credit cards and your loan applications on this portal. "
    "Please ask me something in that area."
)
NO_APPLICATION_ANSWER = (
    "I could not find any loan application on your account yet. "
    "You can start one from the Apply page; once it is submitted I can tell you its status and next steps."
)

ROUTER_SYSTEM = """You route messages for a bank's loan assistant. Reply with one JSON object only, no prose.

Fields:
- "standalone_question": the user's latest message rewritten as one complete English question that makes sense without the conversation. Resolve references like "it", "now", "that one" and translate Hindi/Hinglish. Keep the user's meaning. Do not answer it.
- "intent": exactly one of
  "my_application": about the user's OWN loan application — its status, why it was rejected or failed, what to do next about it, when they will get the money, delays, reapplying after their rejection.
  "emi": calculate a monthly EMI / instalment for a loan amount.
  "policy": general rules — interest rates, fees, charges, penalties, documents, eligibility, foreclosure, credit cards, complaints, RBI rules.
  "small_talk": greetings or thanks only.
  "out_of_scope": unrelated to loans, credit cards or banking.
- "loan_amount": amount in rupees as a number if stated anywhere in the conversation (5 lakh = 500000, 1 crore = 10000000), else null.
- "tenure_months": loan tenure in months if stated (3 years = 36), else null.
- "annual_rate_percent": annual interest rate in percent if stated (1% per month = 12), else null.
"""

# Grounded in the application state machine (routers/applications.py, routers/officer.py) — not invented policy.
STATUS_GUIDE = {
    "draft": ("Not submitted yet.", "Complete all fields and submit it from My Applications; pre-qualification runs automatically on submit."),
    "submitted": ("Submitted; automated pre-qualification is running.", "Check back shortly for the pre-qualification result."),
    "pre_qual_pass": ("Passed automated pre-qualification and is waiting for a loan officer to pick it up.", "No action needed; an officer reviews it next. The portal does not define a fixed review timeline."),
    "manual_review": ("Pre-qualification score was borderline, so it is waiting for a loan officer's manual review.", "No action needed; an officer reviews it next. The portal does not define a fixed review timeline."),
    "pre_qual_fail": ("Did not pass automated pre-qualification, so it will not go to a loan officer. The FAILED rules below are the reasons.", "Address the failed criteria, then submit a new application from the Apply page."),
    "under_review": ("A loan officer is reviewing it now.", "Wait for the officer's decision; it will appear on My Applications."),
    "approved": ("Approved by a loan officer.", "The lender will contact you about the loan agreement and disbursement."),
    "rejected": ("Rejected by a loan officer. The officer's notes, if any, give the reason.", "You can submit a new application from the Apply page. If you believe the decision was wrong, raise a complaint (see the policy passages for how)."),
    "withdrawn": ("Withdrawn by you.", "You can submit a new application from the Apply page."),
}


@dataclass
class Route:
    standalone: str
    intent: str
    amount: float | None = None
    months: int | None = None
    rate: float | None = None
    via: str = "llm"  # "llm" or "fallback"


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


async def load_history(db: AsyncSession, user_id: uuid.UUID, session_id: str) -> list[dict]:
    rows = (await db.execute(
        select(ChatMessage)
        .where(ChatMessage.user_id == user_id, ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(MAX_HISTORY_MESSAGES)
    )).scalars().all()
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


async def save_turn(db: AsyncSession, user_id: uuid.UUID, session_id: str, question: str, answer: str, intent: str | None) -> None:
    # Explicit timestamps: both rows share one transaction, so server now() would tie and lose the order.
    now = datetime.now(timezone.utc)
    db.add_all([
        ChatMessage(user_id=user_id, session_id=session_id, role="user", content=question, intent=intent, created_at=now),
        ChatMessage(user_id=user_id, session_id=session_id, role="assistant", content=answer, intent=intent,
                    created_at=now + timedelta(microseconds=1)),
    ])
    await db.commit()


# ---------------------------------------------------------------------------
# Query understanding
# ---------------------------------------------------------------------------


def _number(value, low: float, high: float) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except ValueError:
        return None
    return number if low <= number <= high else None


def parse_route(raw: str, question: str) -> Route:
    """Validate the router's JSON. The model output is untrusted: unknown intents and bad slots are dropped."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return Route(question, "policy", via="fallback")
    if not isinstance(data, dict):
        return Route(question, "policy", via="fallback")
    intent = data.get("intent") if data.get("intent") in INTENTS else "policy"
    standalone = data.get("standalone_question")
    if not isinstance(standalone, str) or not standalone.strip() or len(standalone) > 500:
        standalone = question
    months = _number(data.get("tenure_months"), 1, 600)
    return Route(
        standalone=standalone.strip(),
        intent=intent,
        amount=_number(data.get("loan_amount"), 1, 1e10),
        months=int(months) if months else None,
        rate=_number(data.get("annual_rate_percent"), 0, 60),
    )


async def route(question: str, history: list[dict]) -> Route:
    transcript = "\n".join(
        f"{m['role']}: {m['content'][:ROUTER_MESSAGE_CHARS]}" for m in history[-ROUTER_HISTORY_MESSAGES:]
    ) or "(no earlier messages)"
    messages = [
        {"role": "system", "content": ROUTER_SYSTEM},
        {"role": "user", "content": f"Conversation so far:\n{transcript}\n\nLatest user message:\n{question}"},
    ]
    try:
        raw = await groq_chat(messages, model=ROUTER_MODEL, name="rewrite-and-route",
                              response_format={"type": "json_object"}, max_tokens=300)
    except Exception:
        logger.warning("Router call failed; falling back to policy search", exc_info=True)
        return Route(question, "policy", via="fallback")
    return parse_route(raw, question)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def _quoted(value: str | None) -> str:
    """User- or staff-typed text as one inert JSON string: no tags, no line breaks, capped length.

    These fields (business name, loan purpose, officer notes) end up in the LLM prompt; quoting keeps
    "ignore your rules..." typed into a form as data, and it cannot close the <application_context> tag.
    """
    if not value or not value.strip():
        return "not specified"
    return json.dumps(value.strip().replace("<", "‹").replace(">", "›")[:300], ensure_ascii=False)


def _describe(app: LoanApplication, index: int, now: datetime) -> str:
    meaning, next_step = STATUS_GUIDE.get(app.status, ("", ""))
    name = app.business_name if app.product_type == "business_loan" else app.employer_name
    lines = [
        f"Application {index}: {PRODUCT_LABELS.get(app.product_type, app.product_type)}",
        f"Name on application: {_quoted(name)}",
        f"Product: {PRODUCT_LABELS.get(app.product_type, app.product_type)}",
        f"Status: {app.status.replace('_', ' ').title()}",
        f"What this status means: {meaning}",
        f"Next step: {next_step}",
    ]
    if app.product_type != "credit_card":
        lines += [
            f"Loan amount requested: ₹{app.loan_amount_requested:,.0f}" if app.loan_amount_requested else "Loan amount requested: not specified",
            f"Loan purpose: {_quoted(app.loan_purpose)}",
        ]
    lines += [
        f"Created on: {app.created_at:%d %b %Y} ({(now - app.created_at).days} days ago)",
        f"Last updated: {app.updated_at:%d %b %Y}",
    ]
    if app.reviewed_at:
        lines.append(f"Officer decision on: {app.reviewed_at:%d %b %Y}")
    if app.review_notes:
        lines.append(f"Officer review notes: {_quoted(app.review_notes)}")
    prequal = app.prequal_result
    if prequal:
        lines.append(f"Pre-qualification score: {prequal.score}/100, decision: {prequal.decision.replace('_', ' ')}")
        for rule in prequal.rule_breakdown or []:
            outcome = "PASSED" if rule.get("passed") else (
                "FAILED (hard eligibility requirement)" if rule.get("knockout") else "FAILED"
            )
            lines.append(f"  - {rule.get('rule', 'rule')}: {outcome} — {rule.get('message', '')}")
    return "\n".join(lines)


async def application_context(db: AsyncSession, user: User, application_id: uuid.UUID | None = None) -> str | None:
    """The caller's own applications only — ownership is a WHERE clause, never an LLM decision."""
    stmt = (
        select(LoanApplication)
        .options(selectinload(LoanApplication.prequal_result))
        .where(LoanApplication.applicant_id == user.id)
    )
    if application_id:
        stmt = stmt.where(LoanApplication.id == application_id)
    else:
        stmt = stmt.order_by(LoanApplication.updated_at.desc()).limit(MAX_CONTEXT_APPLICATIONS)
    apps = (await db.execute(stmt)).scalars().all()
    if not apps:
        return None
    now = datetime.now(timezone.utc)
    header = "The user's loan applications (most recently updated first):"
    return "\n\n".join([header, *(_describe(a, i, now) for i, a in enumerate(apps, 1))])


def emi_answer(r: Route) -> dict:
    missing = [
        label for label, value in (
            ("loan amount", r.amount), ("tenure (months or years)", r.months), ("annual interest rate", r.rate),
        ) if value is None
    ]
    if missing:
        return policy_qa._direct(
            f"To calculate your EMI I need the {', '.join(missing)}. "
            "For example: \"5 lakh for 3 years at 12%\". You can also ask me for the published personal or business loan interest rates.",
            "emi calculator",
            "clarify",
        )
    principal = Decimal(str(r.amount))
    monthly = emi(principal, Decimal(str(r.rate)) / 100, r.months)
    total = monthly * r.months
    return policy_qa._direct(
        f"For a loan of ₹{principal:,.0f} at {r.rate:g}% per annum over {r.months} months, the EMI is ₹{monthly:,.2f} per month. "
        f"Total payable is ₹{total:,.2f}, of which ₹{total - principal:,.2f} is interest. "
        "Formula: EMI = P × r × (1+r)^n / ((1+r)^n − 1), where r is the monthly rate. "
        "This is an estimate; the rate you are offered depends on the lender's assessment of your profile.",
        "emi calculator",
        "calculator",
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def prepare(
    question: str,
    history: list[dict],
    user: User,
    db: AsyncSession,
    application_id: uuid.UUID | None = None,
    on_stage: policy_qa.StageCallback | None = None,
) -> dict:
    """Returns the same 'prepared' shape as policy_qa.prepare_answer, plus intent and route_via."""
    stage = on_stage or (lambda _msg: None)
    early = policy_qa.precheck(question, history)
    if early:
        return {**early, "intent": "precheck", "route_via": "rules"}

    stage("Understanding your question...")
    r = await route(question, history)
    # The rewrite is model output; if it trips the input guard, search with the user's own words.
    if not check_input(r.standalone)[0]:
        r.standalone = question
    stage(f"Routing to: {r.intent.replace('_', ' ')}")

    prepared = await _dispatch(question, history, user, db, application_id, r, on_stage)
    return {**prepared, "intent": r.intent, "route_via": r.via}


async def _dispatch(question, history, user, db, application_id, r: Route, on_stage) -> dict:
    if r.intent == "out_of_scope":
        return policy_qa._direct(OUT_OF_SCOPE_ANSWER, "out of scope", "out_of_scope")
    if r.intent == "small_talk":
        return policy_qa._direct(policy_qa.SMALL_TALK_ANSWER, "general assistant response", "small_talk")
    if r.intent == "emi":
        return emi_answer(r)

    context = None
    if r.intent == "my_application" or application_id:
        context = await application_context(db, user, application_id)
        if r.intent == "my_application" and context is None:
            return policy_qa._direct(NO_APPLICATION_ANSWER, "your applications", "no_application")
    # A selected application narrows policy search to its product; ownership is a WHERE clause on the JWT user.
    product = None
    if application_id:
        product = (await db.execute(
            select(LoanApplication.product_type)
            .where(LoanApplication.id == application_id, LoanApplication.applicant_id == user.id)
        )).scalar_one_or_none()
    # Policy search runs for my_application too: "what should I do now" needs both the record and the rules.
    return await run_in_threadpool(
        policy_qa.prepare_answer, question, history, on_stage, context, r.standalone, product
    )


if __name__ == "__main__":
    # Self-check: python -m app.assistant
    good = parse_route('{"standalone_question": "Why was my loan application rejected?", "intent": "my_application", '
                       '"loan_amount": "5,00,000", "tenure_months": 36, "annual_rate_percent": null}', "why rejected")
    assert (good.intent, good.amount, good.months, good.rate, good.via) == ("my_application", 500000.0, 36, None, "llm")
    assert parse_route("not json", "q") == Route("q", "policy", via="fallback")
    assert parse_route('{"intent": "drop_tables", "standalone_question": ""}', "q").intent == "policy"
    assert parse_route('{"intent": "emi", "tenure_months": 9999, "annual_rate_percent": true}', "q").months is None
    assert emi_answer(Route("q", "emi", amount=500000))["status"] == "clarify"
    done = emi_answer(Route("q", "emi", amount=500000, months=36, rate=12))
    assert done["status"] == "calculator" and "₹16,607.15" in done["answer"], done["answer"]
    print("assistant self-check passed")
