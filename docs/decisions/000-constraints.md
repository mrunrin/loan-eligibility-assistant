# ADR-000 — Project Constraints

**Status:** Accepted  
**Date:** 2026-10-21

## Context

Every architecture decision in this project is made against a fixed set of hard constraints. They are recorded here once so that all other ADRs can reference them instead of re-explaining the reasoning each time.

## Constraints

| Constraint | Value | Impact |
|---|---|---|
| Budget | ₹0 — free tiers and open source only | No paid APIs, no cloud spend, no licenses |
| Hardware | Windows 11 laptop, 16 GB RAM total | Only ~7 GB usable by the app stack (Docker Desktop + WSL overhead ~1 GB, Windows baseline ~5–6 GB) |
| Team | 1 developer (junior), solo | No code review, no pair programming, no on-call rotation |
| Data | Synthetic only — no real customer PII ever enters the system | Free-tier external APIs (Groq, Langfuse) are acceptable because no real data is sent |
| Goal | Production-*shaped* demo for an Indian fintech use case | Correct architecture and security model, acknowledged demo trade-offs documented per ADR |
| Audience | VP-level GenAI / engineering interviewers | Every decision must be explainable; "it was free" is not enough — the production path must also be named |

## What "production path" means in each ADR

Each ADR has a *Production path* line. It names what a bank would actually use if this were a live system. The purpose is to show that the demo choice was a deliberate, informed trade-off — not ignorance of the alternative.

## Consequence

Any recommendation that requires money, more than ~1 GB of additional RAM, a second developer, or real customer data is out of scope for this project's implementation. It goes in the *Production path* line only.
