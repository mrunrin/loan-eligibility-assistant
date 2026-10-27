# ADR-006 — Groq Free Tier for Answers and Routing

**Status:** Accepted (with server-side masking)  
**Date:** 2026-10-21

## Context

The app needs a generative LLM to synthesize RAG answers. See [ADR-000](000-constraints.md) for constraints: ₹0 budget, ~7 GB RAM usable.

## Decision

Use Groq's free tier for both model calls in the chat pipeline (`app/llm.py`):
- **Answers:** `GROQ_MODEL`, default `openai/gpt-oss-20b`.
- **Rewrite + intent routing:** `ROUTER_MODEL`, default `qwen/qwen3.8-27b` (see [ADR-018](018-intent-routing.md)). Groq rate limits are per model, so routing does not use up the answer model's tokens-per-minute.

429 responses are retried up to 3 times, honouring `retry-after` (capped at 10 s).

What reaches Groq is reduced before the prompt is built:
- Personal identifiers in the question (Aadhaar, PAN, card, phone, email) are removed at the API boundary ([ADR-012](012-guardrails.md)).
- Application context is **masked server-side** (`_mask_context()` in `app/policy_qa.py`): the business/employer name → `[the applicant]`, exact amounts → rounded bands (`~₹50L`, `~₹1Cr`), credit score → band (`Good (700-749)`).
- User-typed fields (loan purpose, officer notes) are inserted as quoted, tag-free data so they cannot act as instructions.

The system only operates on **synthetic data** (see ADR-000). No real customer PII is ever in the system.

**Per-borrower daily token budget (optional):** `GROQ_DAILY_BUDGET_TOKENS` (default 0 = off, so demos never stall; set e.g. 50,000 to enable). Every Groq call adds the `usage.total_tokens` that Groq reports (router and answer, streaming included) to a per-request meter; the total is charged to the borrower after the request, even if the client disconnects. A request is allowed while the borrower is under budget; the day resets at midnight IST. Officers and admins are unlimited.

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| OpenAI / Azure OpenAI | Paid. Breaks ₹0 constraint. Azure OpenAI India would be the production choice. |
| Local Ollama (primary) | A 7B model gives noticeably worse answers. A 70B model needs ~40 GB RAM — doesn't fit. |
| Hugging Face Inference API | A third outside data processor with no quality advantage over Groq. Dropped. |
| Self-hosted LLM via llama.cpp | ~4–8 GB RAM leaves no headroom for the rest of the stack. 15–20 token/s is too slow for a live demo. |

## Consequences

- **Free tier rate limits:** Groq's free tier has daily token limits. If one session exhausts the quota, all users are blocked until the next day. Mitigated by the optional per-borrower daily budget above (off by default for the demo) and the per-user request rate limit ([ADR-015](015-rate-limiting.md)). There is no offline answer fallback; all model work runs on hosted APIs ([ADR-020](020-hosted-embeddings-rerank.md)) and an unavailable Groq returns a clear error.
- **Budget storage:** the budget is kept in process memory (one backend worker); a restart resets it. The last request of the day can overshoot by its own size, because its cost is only known afterwards.
- **Data residency:** Groq serves from the US. Masked synthetic data is not a compliance risk for this demo; in production, a private in-region endpoint is required.
- **Latency:** Groq's inference is fast (~200–400 token/s). Acceptable for a demo.

## Production path

Azure OpenAI Service (India region, `eastindia`) with zero data retention enabled, behind an APIM gateway that enforces per-user spend limits and logs every prompt hash for audit.
