# ADR-018 — Query Understanding + Tool Routing for the Chat Assistant

**Status:** Accepted
**Date:** 2026-10-22
**Supersedes:** the single-path "every question → policy RAG" chat flow, and the in-memory `_HistoryStore`

## Context

Live testing with how borrowers actually type ("my application got rejected why", "what should i do now", "how much emi for 5 lakh") showed that the policy RAG was **not** the weak part. It answered every real policy question correctly, including Hinglish ones. The failures were questions whose answer **is not in any policy document**:

| Question type | Where the answer actually lives |
|---|---|
| "why was my application rejected", "when will I get my loan" | The user's own record in Postgres (status, pre-qual rule breakdown, officer notes) |
| "what should I do now" | The conversation so far, plus the user's status, plus a policy rule (complaint escalation) |
| "how much EMI for 5 lakh" | A formula, not a document |
| "capital of France" | Nowhere: it should be refused as out of scope |

Searching PDFs harder cannot answer these. Production assistants (bank chatbots, Klarna-style support bots) **understand the question first, then send it to the right tool**.

A second problem: follow-ups like "what should i do now" were searched as the literal string, which matches nothing. The history reached the answer prompt but **never reached retrieval**.

## Decision

Insert a query-understanding step and a small set of tools in front of the existing RAG. The RAG itself is unchanged.

```
user message + history (Postgres: chat_messages)
        │
        ▼
0. AskRequest validator — personal identifiers replaced with placeholders (ADR-012), so they never
        │            reach either model, the traces, the logs or chat_messages.   (added 2026-10-23)
        ▼
1. policy_qa.precheck  — guardrails (injection / topic), name memory, greetings.  No LLM.
        ▼
2. assistant.route — ONE call to ROUTER_MODEL (Groq, JSON mode, temperature 0)
        │   → standalone_question  ("what should i do now" → "What should I do now after my loan application was rejected?")
        │   → intent: policy | my_application | emi | small_talk | out_of_scope
        │   → slots: loan_amount, tenure_months, annual_rate_percent (filled from the whole conversation)
        │   Output is untrusted: parse_route() whitelists the intent, bounds every number,
        │   and any failure falls back to intent=policy with the raw question (= the old behaviour).
        ▼
3. Tools
   • policy        → policy_qa.prepare_answer(search_query=standalone)   hybrid BM25 + dense + rerank (unchanged)
   • my_application→ assistant.application_context(db, user)       Postgres, WHERE applicant_id = current user
                     + policy search on the same standalone question (record + rules, e.g. complaint path)
                     User-typed fields are quoted as inert data; names are masked before Groq.
                     A selected application also limits policy search to its product (+ general rules).
   • emi           → prequal.emi()  deterministic; asks for missing amount/tenure/rate instead of guessing
   • small_talk / out_of_scope → fixed text, no LLM call
        ▼
4. policy_qa.generate  — GROQ_MODEL writes the answer from tool output only, with [S#] citations; output PII guard.
        ▼
5. assistant.save_turn → chat_messages (survives restarts and multiple workers)
```

## Before vs after — what changed, file by file

| Area | Before | After | Why |
|---|---|---|---|
| **Chat flow** (`app/assistant.py`, new) | Every question → policy RAG | precheck → route → tool → grounded answer | The answer for "why rejected" is in the DB, not the PDFs |
| **Follow-up handling** | Retrieval searched the literal "what should i do now" | Retrieval searches the router's standalone rewrite; the prompt keeps the user's words plus an "Interpreted as" line | Follow-ups need the conversation resolved *before* search |
| **Application data** (`frontend/pages/8_Chat_Assistant.py`, `main.py`) | Browser built an `application_context` text blob and sent it; the server trusted it | Client sends only `application_id`; server loads it with an owner-scoped query | A client could send "Status: Approved" and the bot would repeat it; IDOR-safe by construction |
| **Application tool without a selection** | No data unless the user picked an app in the dropdown | Router detects "my application" questions and loads the user's 3 most recent applications | Users don't pick from dropdowns before asking "why was I rejected" |
| **Status meaning / next step** (`STATUS_GUIDE`) | — | Every status has a plain meaning and a next step, taken from the real state machine in `routers/applications.py` and `routers/officer.py` | "What should I do now" needs an answer grounded in what the portal actually allows |
| **EMI** (`app/prequal.py`) | `estimated_emi()` fixed at 12% / 36 months, used only by pre-qual | General `emi(principal, rate, months)`; pre-qual keeps the same result; chat uses it with slot filling | An LLM doing compound-interest arithmetic is a hallucination risk |
| **Out of scope** | "The policy does not specify that." (searched the PDFs for France) | Polite scope message, no retrieval, no answer-model call | Correct refusal; saves an LLM call |
| **Chat memory** (`models.py`, `migrations/008`) | `_HistoryStore`: in-process dict, lost on restart, one worker only | `chat_messages` table, indexed (user_id, session_id, created_at); CASCADE on account delete | Production memory must survive deploys and scale-out; deletion is a privacy requirement |
| **Groq resilience** (`llm.groq_chat`, `llm.stream_completion`) | One attempt; a 429 became "AI service is temporarily unavailable" | Retries 429 up to 3× honouring `retry-after` (capped at 10 s) | The earlier test run lost 3 answers to rate limits |
| **Dead code** | Sync `_call_groq()` unused | Removed | — |
| **Prompt** (`policy_qa._prompt`) | One refusal rule: "if no passage is relevant, reply exactly…" | When application data is present, the refusal applies only if neither passages nor application data answer; status questions are answered from the record | The exact-refusal rule was overriding the application data |
| **Audit / response** | — | `intent` added to the `/ask` response, the SSE `done` event, Langfuse output and `audit.jsonl` | You can see and measure which tool answered each question |
| **Config** (`config.py`, `.env.example`) | — | `ROUTER_MODEL` (default `qwen/qwen3.8-27b`) | Groq rate limits are per model, so routing uses its own tokens-per-minute bucket |
| **Tests** (`app/tests/test_assistant.py`, new) | — | Router output validation, EMI slot filling, owner scoping (including another user's `application_id`), history order across sessions | — |

## Results — same conversations, before and after

**Setup:** three throwaway borrowers created for the test and deleted afterwards:
- **A:** passed pre-qual, then rejected by an officer with notes about missing GST returns.
- **B:** failed pre-qual on 4 rules.
- **C:** no applications.

All questions in a thread share one `session_id`. **Before** = the same build with the router failing, which runs exactly the old pipeline (every question to policy RAG, no application data). **After** = the router enabled.

### Thread A — officer-rejected borrower (one session)

| # | Question | Before | After | Intent |
|---|---|---|---|---|
| 1 | when will i get my loan after applying | ❌ "does not specify" | ✅ Rejected by officer, officer's reason, reapply or complain | my_application |
| 2 | i applied 3 days ago and no update yet whats happening | ❌ "does not specify" | ✅ Status + reason + next step | my_application |
| 3 | my application got rejected why | ❌ "does not specify" | ✅ Officer notes: GST returns missing, irregular cash deposits | my_application |
| 4 | what should i do now *(memory test)* | ❌ no sources | ✅ Reapply, or complain to the lender then RBI CMS within 30 days [S1] | my_application |
| 5 | can i reapply again or is it permanently rejected | ❌ no sources | ✅ Not permanent; reapply; escalation path [S1] | my_application |
| 6 | how much emi will i pay if i take 5 lakh loan | ❌ "does not specify" | ✅ Asks for tenure and rate (slot filling) | emi |
| 7 | 12% for 3 years *(memory: amount from turn 6)* | ❌ "does not specify" | ✅ ₹16,607.15/month, total ₹5,97,857.40, formula shown | emi |
| 8 | what docs do i need to submit | ⚠️ Credit-card KYC clause (wrong document) | ⚠️ "does not specify" | policy |
| 9 | bhai interest rate kitna hai business loan pe | ✅ 10.75–22.50% | ✅ 10.75–22.50% | policy |
| 10 | need money urgently can i get fast approval | ✅ "does not specify" (no fast-track exists) | ✅ same | my_application |
| 11 | what is penal interest and when does it apply | ✅ | ✅ (also gives the 2024 implementation dates) | policy |
| 12 | difference between fixed and floating rate | ✅ | ✅ | policy |
| 13 | can i foreclose my loan early | ✅ | ✅ (more complete: 12-EMI lock-in + charges) | policy |
| 14 | credit card interest rate kya hai | ✅ 3.75%/month = 45% p.a. | ✅ same | policy |
| 15 | minimum due on credit card how is it calculated | ✅ | ✅ | policy |
| 16 | can i take personal loan and business loan both at same time | ✅ "does not specify" (not in corpus) | ✅ same | policy |
| 17 | Can I get a home loan? | ✅ "does not specify" | ✅ same | policy |
| 18 | What is the capital of France? | ⚠️ searched PDFs, "does not specify" | ✅ Out-of-scope message, no LLM call, 2.9 s | out_of_scope |

### Thread B — pre-qual-failed borrower (one session)

| Question | Before | After |
|---|---|---|
| my loan application didnt go through, why | ❌ "does not specify" | ✅ Lists the 4 failed rules: credit 610 < 650, 1 year < 2, revenue ~₹9 L vs ~₹30 L, EMI capacity ~₹20 K vs ~₹50 K |
| what should i do now | ❌ no sources | ✅ Fix those 4 criteria and reapply; complaint path with RBI CMS link [S1] |
| mera loan kab tak milega | ❌ no sources | ✅ "Pre Qual Fail: it will not be disbursed; fix the criteria and reapply" |

### Threads C–E

| Case | Before | After |
|---|---|---|
| C: "what is the status of my application" (no applications) | ❌ "does not specify" | ✅ "No application on your account yet; start one from Apply", no LLM call |
| D: `/ask/stream` with `application_id` (frontend path) | — | ✅ Stages stream (`Understanding… → Routing to: my application → …`); failed rules listed |
| E: borrower C sends borrower A's `application_id` | — | ✅ "No application on your account": nothing leaked |

**Score:** 10/23 → **21/23** correct or correctly refused. Both remaining ⚠️ cases are listed under known gaps.

**Memory check:** every turn of the three test runs (144 rows) was written to `chat_messages` and read back on each follow-up. There is no in-process store any more, so a backend restart no longer loses context. Deleting the test users left 0 orphaned rows (CASCADE works).

## Costs

| | Before | After |
|---|---|---|
| LLM calls per policy / application question | 1 | 2 (router ~0.8 s + answer) |
| LLM calls for EMI, out-of-scope, no-application | 1 | 1 (router only); the answer is deterministic |
| Latency, policy question | ~7–9 s | ~8–10 s |
| Latency, EMI / out-of-scope | ~7–9 s | **~3 s** |
| Rate-limit exposure | answer model only | Router uses a **separate per-model Groq bucket**, and 429s are retried |
| New infrastructure | — | one table, one migration; no new dependency |

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Append the previous question to the search string | Breaks on the first topic switch; no intent, so "why rejected" still searches PDFs. A patch, not an architecture. |
| Autonomous agent (ReAct / LangGraph multi-step tool loop) | 3–6 LLM calls per turn on a free-tier TPM budget; non-deterministic paths are hard to test and audit. One routing step covers all observed intents. |
| Text-to-SQL for application questions | The LLM would write queries against PII tables: IDOR and injection risk. Fixed, parameterised tools with the user id taken from the JWT are the production pattern. |
| Native function/tool calling instead of a JSON router | Equivalent capability, but it ties routing to the answer model and its rate bucket. JSON-mode routing on a separate small model is cheaper and model-agnostic. Worth revisiting if the answer model changes. |
| Keyword/regex intent classifier | Cannot resolve "what should i do now" or Hinglish; would need constant maintenance. |
| Vector "memory" of past chats | The source of truth for "long-term memory" is the application record in Postgres, which can be audited. Embedding chat logs adds PII risk and no accuracy. |
| `llama-3.1-8b-instant` as router | Returned 404: not available to this Groq key. `qwen/qwen3.8-27b` and `openai/gpt-oss-120b` were both 6/6 on the routing probe; Qwen was faster (~0.8 s vs ~1.1 s). |

## Known gaps (not fixed here, by choice)

1. **"what docs do i need to submit"**: the rewrite is correct, but retrieval ranked agreement clauses above the HDFC documentation pages (now merged into `hdfc_*.md`). The question is also ambiguous (personal vs business loan). Partly addressed (2026-10-23): when the borrower selects an application in the chat, search is limited to that product's chunks plus general rules. Still open: a `product` slot in the router for questions asked without a selection, and retrieval recall on documentation queries (`eval/golden_retrieval.yaml`).
2. **"need money urgently, fast approval"**: "does not specify" is truthful (no fast-track process exists) but unhelpful. Production fix: an ops FAQ document (processing SLAs, expedite policy). This is a content gap, not a code gap.
3. **SLA / timeline questions** get "no fixed review timeline is defined", because the portal genuinely defines none. Add real SLAs to `STATUS_GUIDE` once the business sets them.
4. **History retention**: `chat_messages` grows without limit. Production: a retention job (e.g. 90 days) and a rolling summary once a session exceeds `MAX_HISTORY_MESSAGES`.
5. The router sees up to 6 prior messages (400 chars each). Very long sessions rely on the recent window only.

## Production path

- Router: the same design with a hosted small model, or a fine-tuned classifier once enough labelled traffic is in `audit.jsonl` (`intent` is now logged).
- Add tools the same way: `document_checklist(product)`, `repayment_schedule(loan_id)`, `raise_complaint()`, `handoff_to_human()`, each a parameterised function scoped to the JWT user.
- Memory: keep Postgres as the source of truth; add Redis as a read-through cache when running multiple workers.
- Evaluation: turn the threads above into a conversational eval set (multi-turn promptfoo tests asserting `intent` + key facts) and gate CI on it.

## How to verify

```bash
python -m app.assistant                     # router parsing + EMI self-check
python -m pytest app/tests -q               # incl. owner scoping, history order, inert prompt data
alembic upgrade head                        # applies all migrations (008 added chat_messages)
```
Then in the Chat Assistant page: ask "why was my application rejected", then "what should i do now"; the status box shows `Routing to: my application`.
