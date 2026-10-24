# HDFC Loan Bot Evaluation

Two automated gates plus a manual review set.

## Files

- `golden_retrieval.yaml` — 9 graded questions (2 easy, 3 medium, 4 hard) over the RBI and HDFC markdown files and the credit-card MITC, each with the gold (document, page) that answers it. Hard questions have a distractor document that shares their vocabulary.
- `retrieval_eval.py` — runs the real hybrid retriever and reports **recall@3** and **MRR** (k is
  `FINAL_EVIDENCE_K`, the number of passages the answer model sees); exits 1 below
  `RECALL_THRESHOLD` (default 0.80). Needs `JINA_API_KEY` and `COHERE_API_KEY`; no LLM calls.
- `questions.yaml` — 15 Promptfoo answer-level cases (facts, missing-policy handling, lender filtering,
  guardrails, startup cold-start regression). Every case also runs the numeric-faithfulness check
  defined in `promptfooconfig.yaml`: each number in the answer must appear in the cited evidence or
  the question.
- `check_pass_rate.py` — gates on the overall Promptfoo pass rate (`PASS_RATE_THRESHOLD`, default 0.9),
  so one flaky LLM answer does not fail CI on its own.
- `test_questions.md` — human-readable manual review set.

## Run locally

```powershell
python eval/retrieval_eval.py
```

Start the backend, log in as an officer or admin to get an access token (see the token budget note below), then:

```powershell
$env:EVAL_ACCESS_TOKEN = "<access token>"
npx promptfoo@0.102.0 eval --no-cache -o eval/promptfoo_results.json
python eval/check_pass_rate.py eval/promptfoo_results.json
```

Promptfoo calls `http://localhost:8000/ask` (answers come from Groq, so `GROQ_API_KEY` must be set) with a
separate chat session per question, so the server-side conversation memory cannot leak between cases.
Each call counts against the eval user's daily token budget; use an officer or admin token, which is unlimited.

## What the gates check

- Retrieval: the right page reaches the top 3 (recall@3) and how high it ranks (MRR).
- Answers: key facts present, "the policy does not specify that" for blank or missing facts, no numbers
  outside the evidence, lender-specific questions cite only that lender, PII in the question is removed
  (with a notice) rather than refused, and prompt injection and off-topic requests are refused.

After a `CHUNKER_VERSION` bump (v4 added query/passage task prefixes and chunk metadata), re-run the retrieval eval
and compare recall@3 / MRR with the previous run.

These sets are small. They catch regressions; they do not prove production-grade accuracy.
