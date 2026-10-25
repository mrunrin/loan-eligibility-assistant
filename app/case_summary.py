"""Short case summary of a pre-qualification result for loan officers: one Groq call, rules fallback."""

import logging

from app.llm import groq_chat, provider_ready
from app.models import PRODUCT_LABELS, LoanApplication, PreQualificationResult
from app.policy_qa import _mask_context

logger = logging.getLogger(__name__)
MAX_LINES = 6

# (application id, pre-qual run time) → summary. A re-run gets a new key, so its summary is fresh.
# shortcut: in-process cache, lost on restart and per worker; move to a DB column if summaries must persist.
_cache: dict[tuple[str, str], dict] = {}

PROMPT = """You are summarising a loan pre-qualification result for a loan officer.
Write {n} short bullet lines (each starting with "- ", at most 25 words) covering: what is being applied for,
the outcome, which rules failed and why, which figures are self-reported and unverified, and what the officer
should check in the documents. Use only the facts below. Do not recommend approving or rejecting.

{facts}"""


def _rule_lines(prequal: PreQualificationResult) -> list[str]:
    lines = []
    for rule in prequal.rule_breakdown or []:
        outcome = "PASSED" if rule.get("passed") else (
            "FAILED (hard eligibility requirement)" if rule.get("knockout") else "FAILED"
        )
        unverified = " [self-reported, unverified]" if rule.get("unverified") else ""
        lines.append(f"- {rule.get('rule', 'rule')}: {outcome}{unverified} — {rule.get('message', '')}")
    return lines


def _facts(app: LoanApplication, prequal: PreQualificationResult) -> str:
    head = [
        f"Product: {PRODUCT_LABELS.get(app.product_type, app.product_type)}",
        f"Status: {app.status.replace('_', ' ')}",
        f"Pre-qualification score: {prequal.score}/100, decision: {prequal.decision.replace('_', ' ')}",
        "Rules:",
    ]
    # Amounts and credit scores become bands before leaving the process, as in the chat assistant.
    return _mask_context("\n".join(head + _rule_lines(prequal)))


def fallback_summary(app: LoanApplication, prequal: PreQualificationResult) -> list[str]:
    """The same facts as plain sentences, used when Groq is not configured or fails."""
    rules = prequal.rule_breakdown or []
    failed = [r for r in rules if not r.get("passed")]
    lines = [
        f"{PRODUCT_LABELS.get(app.product_type, 'Application')} scored {prequal.score}/100 "
        f"({prequal.decision.replace('_', ' ')})."
    ]
    lines += [
        f"Failed{' (eligibility requirement)' if r.get('knockout') else ''}: {r.get('rule')} — {r.get('message')}"
        for r in failed[: MAX_LINES - 3]
    ]
    if not failed:
        lines.append("All pre-qualification rules passed.")
    if any(r.get("unverified") for r in rules):
        lines.append("Credit score, income and EMI figures are self-reported and unverified.")
    lines.append("Check the uploaded documents before deciding.")
    return lines[:MAX_LINES]


def _bullets(text: str) -> list[str]:
    lines = [ln.strip().lstrip("-*•").strip() for ln in text.splitlines() if ln.strip()]
    return [ln for ln in lines if ln][:MAX_LINES]


async def case_summary(app: LoanApplication, prequal: PreQualificationResult) -> dict:
    key = (str(app.id), prequal.ran_at.isoformat() if prequal.ran_at else "")
    if key in _cache:
        return _cache[key]
    lines: list[str] = []
    if provider_ready()[0]:
        try:
            prompt = PROMPT.format(n=MAX_LINES, facts=_facts(app, prequal))
            lines = _bullets(await groq_chat(
                [{"role": "user", "content": prompt}], name="case-summary", reasoning_effort="low", max_tokens=600,
            ))
        except Exception:
            logger.exception("AI case summary failed; using the rules summary")
    result = {"ai": bool(lines), "lines": lines or fallback_summary(app, prequal)}
    if result["ai"]:
        _cache[key] = result  # cache only real AI summaries, so a transient Groq failure is retried next time
    return result
