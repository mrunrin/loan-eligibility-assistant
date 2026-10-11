# LoanBot Evaluation Questions

Use this set for manual demo testing and Promptfoo coverage. Record actual outputs after running the Streamlit UI or `/ask` endpoint.

| # | Question | Expected Answer (key phrase) | Expected Source | Result | Notes |
|---|---|---|---|---|---|
| 1 | What is the foreclosure or prepayment charge? | Specific charge if present, or charge terms from the agreement | MBBL / business loan agreement clause on foreclosure or prepayment | Pending | Check whether the answer is grounded in the shown excerpt. |
| 2 | On what date is the EMI due each month? | 11th of every month, if schedule evidence is retrieved | MBBL PDF schedule page | Pending | Should not say missing if schedule passage is retrieved. |
| 3 | Can a borrower dispute EMI calculation and withhold payment? | No; obligation to pay EMI is absolute and unconditional | MBBL clause around EMI dispute / Clause 3.9 | Pending | Good faithfulness test. |
| 4 | What happens if a borrower stops payment on a cheque provided for the loan? | Event of Default | Event of Default clause | Pending | Tests BM25 exact phrase matching. |
| 5 | Can the bank transfer or assign the loan to a third party? | Yes, transfer/assignment rights as stated | Assignment / transfer clause | Pending | May require both semantic and keyword retrieval. |
| 6 | How long can a borrower travel abroad for a short visit? | 60 days | Covenant / travel abroad clause | Pending | Tests buried clause retrieval. |
| 7 | What are the borrower's obligations regarding insurance? | Maintain/provide insurance if specified | Insurance covenant clause | Pending | Watch for invented generic insurance advice. |
| 8 | Under what conditions can the bank recall the loan immediately? | Event of Default / recall conditions | Event of Default / recall clause | Pending | Multi-clause answer may cite multiple excerpts. |
| 9 | What is the exact rate of interest on the loan? | The policy does not specify that, if only blank template fields exist | Schedule / interest field | Pending | Blank template fields should not become invented numbers. |
| 10 | Can I get a home loan? | Out-of-scope or policy does not specify | No policy source needed unless retrieved evidence supports it | Pending | Tests safe handling of unsupported questions. |

## Known Manual Review Criteria

- Answer should use only facts present in the displayed sources.
- Source excerpt should directly support the answer.
- If source evidence is absent or blank, answer should say the policy does not specify it.
- Local `qwen3.5:2b` may answer more tersely than Groq/Hugging Face models.
