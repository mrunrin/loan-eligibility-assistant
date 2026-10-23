# ADR-005 — JWT Auth with Token Version Revocation

**Status:** Accepted  
**Date:** 2026-10-21

## Context

The app needs authentication and authorization for borrowers, officers, and admins. See [ADR-000](000-constraints.md).

## Decision

JWT access tokens (5 min) + refresh tokens (1 hour, rotated on use), HS256, with `iss` (`hdfc-loan-portal`) and `aud` (`hdfc-loan-portal-api`) claims checked on every decode. A `token_version` INTEGER column on the `users` table makes tokens revocable server-side without a session store.

**How revocation works:**
1. `token_version` (default 0) is embedded in the JWT payload at issue time.
2. `get_current_user()` fetches the user from DB and rejects any token whose `ver` claim differs from the current `token_version`.
3. Logout increments `token_version` — all existing tokens for that user become invalid instantly. Deactivating a user clears their refresh token.

**Refresh-token rotation:** only the SHA-256 of the latest refresh token is stored (`users.refresh_token_hash`). Each refresh issues a new pair; presenting an older, already-rotated token is treated as theft — the live session is revoked and a `refresh_token_reuse` audit event is written.

**Server-side session limits** (added after review; the browser idle timer alone can be bypassed):
- `users.last_seen_at` is updated at most once a minute on authenticated requests; a session idle for `SESSION_IDLE_MINUTES` (15) is rejected on both access and refresh.
- `users.session_started_at` is set at each completed sign-in; refresh fails after `SESSION_MAX_HOURS` (12), so an active user still signs in again at least twice a day.
- The browser additionally logs out after 5 minutes without user activity.

**Brute-force protection:** account lockout after `MAX_FAILED_LOGINS` (5) wrong passwords or `MAX_FAILED_OTP` (5) wrong 2FA codes. Each repeat lockout doubles (15 → 30 → 60 min … capped at 24 h, reset by a successful sign-in) and emails the owner. Separately, one client IP gets 429 after `IP_FAILED_LOGIN_LIMIT` (20) failures in 15 minutes across all accounts, and its attempts stop counting against accounts — so knowing someone's email is not enough to keep them locked out.

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Server-side sessions (Redis) | Adds another service (~100 MB RAM) and a network dependency. JWT is stateless and fits the 7 GB constraint. |
| Short-lived tokens only (no refresh) | 5-min tokens with no refresh would log users out mid-task — bad UX. |
| Opaque tokens (DB lookup on every request) | One extra DB round-trip per request; no benefit over the `token_version` check which is already a DB fetch. |
| python-jose (current dependency) | Effectively unmaintained. Upgrade path: PyJWT (planned). |

## Consequences

- Any environment that issues tokens before `token_version` was added will have tokens with no `ver` claim. `payload.get("ver", 0)` treats missing as 0, which matches the default DB value — backward compatible.
- `token_version` is stored per-user, not per-token. A logout invalidates all sessions for that user on all devices (intentional), and one refresh hash per user means one active session at a time.
- Per-IP failure counters are kept in process memory (one backend worker); a restart clears them.
- Changing the issuer/audience values logs everyone out once.

## Production path

Dedicated identity provider (Keycloak, Azure AD B2C) with WebAuthn for staff MFA, short-lived access tokens (5 min), and an OAuth2 Authorization Code flow. A `sessions` table (one row per device, token families) replaces the single refresh hash; key IDs (`kid`) with asymmetric signing (RS256/EdDSA) once other services verify tokens; CAPTCHA after repeated failures.
