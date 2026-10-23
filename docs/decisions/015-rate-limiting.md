# ADR-015 — Rate Limiting with SlowAPI

**Status:** Accepted  
**Date:** 2026-10-21

## Context

The auth endpoints (`/auth/login`, `/auth/register`) are exposed to the internet in a deployed demo. Without rate limiting, a script can attempt thousands of credential pairs per minute (credential stuffing) or register thousands of fake accounts. The `/ask` endpoint calls Groq on each request — unbounded calls exhaust the free API quota. See [ADR-000](000-constraints.md) and [ADR-013](013-totp-mfa.md).

## Decision

Use **`slowapi`** (FastAPI wrapper around `limits`) with in-memory storage, through one shared limiter in `app/limiter.py`.

**Whose bucket a request counts against.** Browser traffic reaches FastAPI from the Streamlit server, so keying on the TCP peer would put every user in one bucket. Instead:
- Anonymous endpoints key on the **client IP**: Streamlit forwards the browser's IP in `X-Forwarded-For`, and the header is honoured only when the peer is in `TRUSTED_PROXIES`.
- Authenticated endpoints key on the **user id** from the JWT (`user_or_ip`), falling back to the IP.

Limits:
- `/auth/login`, `/auth/register`, `/auth/verify-email` — 10 / minute / IP
- `/auth/refresh` — 30 / minute / IP
- `/auth/resend-verification` — 3 / 4 hours / IP
- 2FA setup / confirm / validate — 5–10 / minute
- `/ask`, `/ask/stream` — 20 / minute / user, for every role (borrowers are additionally subject to the daily token budget — see [ADR-006](006-groq-llm.md))

Separately from slowapi, failed logins are counted per IP across all accounts (`IP_FAILED_LOGIN_LIMIT`, 20 per 15 minutes → 429), see [ADR-005](005-jwt-auth.md).

## Why slowapi, not a custom middleware

`slowapi` is a one-decorator solution that integrates with FastAPI's `HTTPException` and returns RFC 6585-compliant 429 responses with `Retry-After` headers. Writing a custom rate limiter would be ~100 lines for equivalent functionality. This is the definition of a use case for an existing library.

## Why in-memory, not Redis

Redis is the correct backing store for rate limits shared across multiple instances. But the demo runs as a single instance. In-memory storage costs nothing extra, requires no additional service, and is reset on restart — which is acceptable (a restarted server resets the counter, not a security concern at this scale).

The limitation: in-memory limits do not survive restarts and are not shared across instances. Document this as the known limitation.

## Limits chosen

| Endpoint | Limit | Reasoning |
|---|---|---|
| `/auth/login` | 10/min/IP | A human typing a password tries at most 3–4 times per minute. 10 gives legitimate users headroom during a demo while blocking automated scripts. |
| `/auth/register` | 10/min/IP | New account creation is slow in normal use; the same limit as login keeps the config simple. |
| `/ask` | 20/min/user | ~3 questions per 10 seconds is fast for a human; automation would exceed this. |

## Consequences

- Limits are applied with `@limiter.limit(...)` decorators; `main.py` registers slowapi's `RateLimitExceeded` handler, which returns 429.
- Rate limit state is per-process. If the demo restarts mid-presentation, counters reset — not a problem in practice. The backend runs one uvicorn worker, so limits are not multiplied.
- Officers and admins have the same `/ask` rate limit as borrowers but no daily token budget (see [ADR-006](006-groq-llm.md)).

## Production path

Replace in-memory storage with Redis (`slowapi` supports `redis://` as the storage backend with one config change). Add a WAF (AWS WAF, Cloudflare) in front for IP-level blocking before requests reach FastAPI.
