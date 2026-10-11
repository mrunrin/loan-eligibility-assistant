# LoanBot Evaluation

This folder contains the capstone evaluation set used to check answer quality, source faithfulness, and regression risk.

## Files

- `test_questions.md`: human-readable manual review set with expected facts and notes.
- `questions.yaml`: Promptfoo test cases used by the automated quality gate.

## Run Locally

Start the backend first:

```powershell
uvicorn app.main:app --reload
```

Then run Promptfoo from the project root:

```powershell
npx promptfoo@latest eval
```

The Promptfoo config calls `http://localhost:8000/ask` with `model_provider: local`.

## What The Gate Checks

- Key facts appear in the answer.
- Missing-policy questions do not receive invented answers.
- Regressions such as returning "does not specify" when evidence exists are caught.

This is a capstone gate, not a full production evaluation suite. Manual source review is still required before the demo.
