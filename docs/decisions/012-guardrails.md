# ADR-012 — Guardrails: Redact PII, Refuse Injection, Guard the Output

**Status:** Accepted  
**Date:** 2026-10-21 (revised 2026-10-23: PII is redacted instead of blocked)

## Context

The chatbot sends user input to Groq (a US-based LLM API). User input may contain PII (Aadhaar, PAN, card and phone numbers, email), prompt-injection attempts, or requests the chatbot must not answer (medical, legal, investment). The LLM output may leak PII. User-typed application fields (loan purpose, business name) also end up in the prompt. See [ADR-006](006-groq-llm.md) and [ADR-000](000-constraints.md).

## Decision

Layered defence in `app/guardrails_config.py`, applied at the API boundary and around generation:

1. **Input length** — the Pydantic `AskRequest` model rejects questions over `MAX_MESSAGE_CHARS` (2000).
2. **PII redaction at the API boundary** — the `AskRequest` validator replaces personal identifiers with placeholders (`[AADHAAR REDACTED]`, `[PAN REDACTED]`, `[CARD REDACTED]`, `[PHONE REDACTED]`, `[EMAIL REDACTED]`) and the question is still answered. Because this happens first, the raw identifier never reaches the router or answer model, Langfuse, `audit.jsonl` or stored chat history. The response carries a `notice` telling the user an identifier was removed.
   - Aadhaar: 12 digits starting 2–9 in 4-4-4 groups, accepted only if the **Verhoeff checksum** passes.
   - Card: 16 digits in 4-4-4-4 groups, accepted only if the **Luhn checksum** passes.
   - PAN, Indian mobile numbers and email by pattern. Full-width digits are normalised first (NFKC).
   - Checksums keep loan amounts and account numbers from being mistaken for IDs.
3. **Refusals** (`check_input`, on the question and every user turn in the history) — prompt-injection phrasing ("ignore previous instructions", "system prompt", role prefixes) and off-topic subjects (medical/health, legal advice, trading/crypto, personal relationships) get a canned refusal without any model call. Health information (PHI) is therefore never processed.
4. **Prompt hygiene** — retrieved passages, history and application data are wrapped in labelled sections and the prompt states they are data, not instructions. User-typed fields are inserted as quoted, length-capped strings with `<`/`>` neutralised so they cannot close a section; the question and history get the same tag neutralisation.
5. **Output PII guard** — after generation, PII in the answer that does not appear in the retrieved evidence is replaced with a placeholder, on `/ask` and sentence by sentence on `/ask/stream`. The same checksum rules apply.

Traces sent to Langfuse pass through the same redaction (`app/tracing.py`).

## Why redact instead of block

The earlier version refused any message containing an identifier. "My PAN is ABCDE1234F, am I eligible?" got a canned refusal, although the question itself is fine. Removing the identifier protects the data just as well and still helps the user.

## Why layers, not one

A single prompt instruction ("never output PII") is a soft guardrail — it relies on the model following instructions correctly on every call. Defence in depth: deterministic input checks before any LLM call (zero cost), prompt rules for nuanced cases, and a deterministic output check for anything the model still emits.

## Why regex, not a classifier

A PII classifier (a fine-tuned BERT model) would add ~200–400 MB RAM and 1–2 seconds per call for a task that regex plus checksums solves in < 1 ms for the identifiers we care about. The trade-off: regex misses names, addresses and semantic PII ("my account number ending in 4422") — a documented limitation.

## Canned refusal

"I can only answer questions about loan eligibility, business loan policies, and the application process. Please rephrase your question within that scope." It does not reveal what the blocklist contains.

## Consequences

- The output guard compares against the raw source chunk text, not the masked context, to avoid false positives from the amount-banding masking in [ADR-006](006-groq-llm.md).
- `python -m app.guardrails_config` runs a self-check of the redaction and refusal rules.

## Production path

Microsoft Presidio, AWS Comprehend or Google DLP for names, addresses and other entity types; a model-based topic classifier for off-topic detection. Keep the deterministic output guard as a last-resort backstop.
