# ADR-002 — Streamlit as the Frontend

**Status:** Accepted (demo trade-off)  
**Date:** 2026-10-21

## Context

The project needs a UI layer covering login, registration, loan application (multi-step form), borrower dashboard, officer review queue, admin panel, and a chat assistant. See [ADR-000](000-constraints.md): ₹0 budget, solo Python developer.

## Decision

Use **Streamlit** with the multipage app structure (`frontend/pages/`).

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| React / Next.js | Second language (JavaScript/TypeScript), a Node build pipeline (Vite/webpack), separate deployment, and CORS config between frontend and backend. All of this adds time without adding value to what a VP evaluates — which is the AI pipeline, system design, and security model. |
| Jinja2 + Bootstrap inside FastAPI | Requires writing HTML/CSS templates. That time is better spent on the data and AI layers. No separation of concerns (UI and API in the same process). |
| Gradio | Designed for ML demos, not multi-page portals. No RBAC, no forms with multi-step state. |
| Dash | More verbose than Streamlit for the same output. Primarily a data visualisation framework. |

## Known limitations (documented, not hidden)

1. **JWT in session_state and JS-readable cookies, not httpOnly cookies.** Streamlit communicates via WebSocket, so it cannot set server-issued httpOnly cookies. Tokens are stored in `st.session_state` and sent as `Authorization: Bearer` headers; as a best-effort session restore after a page refresh they are also written to browser cookies set by `extra_streamlit_components` (`hdfc_at` 5 min, `hdfc_rt` 1 hour), which JavaScript can read. In practice the restore rarely succeeds, so a refresh usually returns to the login page; that bank-style behaviour is kept on purpose. An XSS bug could steal them. Mitigations: short token lifetimes, refresh-token rotation with reuse detection, and server-side idle/absolute session limits ([ADR-005](005-jwt-auth.md)). Acceptable for a demo with synthetic data.
2. **No server-side rendering.** Every Streamlit page re-runs the entire Python script on each widget interaction. Not suitable for a high-traffic production frontend.
3. **Token expiry during long sessions.** A 401 response triggers a refresh attempt; if that also fails, the user is redirected to the login page.
4. **Idle logout is enforced twice.** A small script logs the browser out after 5 minutes without mouse, keyboard, scroll or touch activity; because a script can be bypassed, the server also rejects sessions idle for 15 minutes.

## Consequences

- The backend (FastAPI + PostgreSQL + JWT + async SQLAlchemy) is fully production-grade and independent of the frontend choice. A VP reviewing the architecture evaluates the backend, not the Streamlit widgets.
- Replacing Streamlit with React is the obvious production upgrade and is explicitly documented.

## Production path

React (Next.js) frontend served via a CDN, talking to the same FastAPI backend through a backend-for-frontend that holds tokens in `HttpOnly; Secure; SameSite=Strict` cookies. The backend API contract does not change.
