# ADR-013 — TOTP-Based MFA for Staff Accounts

**Status:** Accepted  
**Date:** 2026-10-21

## Context

Officers and admins have access to all loan applications and can approve or reject them. A compromised staff credential without MFA is a single point of failure. Borrower accounts access only their own data — lower risk. See [ADR-005](005-jwt-auth.md) and [ADR-000](000-constraints.md).

## Decision

Mandatory **TOTP MFA** (`pyotp`) for all `officer` and `admin` accounts. Borrowers may turn it on from the Security page; login checks `totp_enabled` per user, so a borrower who enabled it is always asked for a code. Staff who have not enrolled are taken through enrollment on their first login (see below).

## Why TOTP, not SMS OTP

SMS OTP requires an SMS gateway (Twilio, AWS SNS) — a paid service. TOTP is free, works offline, and is more secure (SMS is vulnerable to SIM-swap attacks). Google Authenticator, Authy, and every phone's built-in authenticator app support TOTP.

## Why mandatory, not optional

An optional MFA policy means some staff accounts will never enroll. A single compromised unenrolled account grants an attacker full officer access. Mandatory enrollment closes this gap. The ₹0 budget constraint rules out hardware tokens (YubiKey).

## Implementation

- `users.totp_secret` — base32 TOTP secret generated at enrollment, stored **Fernet-encrypted** (`app.auth.encrypt_secret`). Enrollment writes `totp_pending_secret` first, so `/2fa/setup` can never switch existing 2FA off.
- `users.totp_last_counter` — the last accepted time step; a code cannot be replayed within its window.
- `users.totp_enabled` — boolean; set to true after successful first-time verification.
- `POST /auth/2fa/setup` + `POST /auth/2fa/confirm`: a logged-in user enrolls from the Security page.
- `POST /auth/2fa/validate`: second login step. It exchanges the 5-minute `mfa_pending` token plus a code for real tokens, which carry `token_version`.
- Login flow: if `totp_enabled`, the response is `{"mfa_required": true, "mfa_token": …}` and the frontend asks for the code.

### First-login enrollment for staff (added 2026-10-22)

The original check returned **403** to staff without 2FA. Enrolling needs a session, so a new officer could never log in or enroll: a deadlock. The fix follows the forced-enrollment pattern Okta, AWS IAM and Google Workspace use:

1. Correct password, staff, no 2FA → `{"mfa_enrollment_required": true, "enroll_token": …}`. No session tokens are issued.
2. The `enroll_token` (type `mfa_enroll`, 10 minutes) is accepted **only** by `POST /auth/2fa/enroll/setup` (returns the QR code) and `POST /auth/2fa/enroll/confirm` (first code → 2FA on → real tokens). `get_current_user` rejects any token type other than `access`, so it cannot call the API.
3. Wrong codes count toward the `MAX_FAILED_OTP` lockout (same doubling backoff and owner email as password lockouts, [ADR-005](005-jwt-auth.md)). A locked account cannot use its enrollment token.
4. Staff cannot call `/auth/2fa/disable`, because that would recreate the deadlock.

**Residual risk:** whoever holds the password at first login chooses the authenticator. Production path: an admin-issued, single-use enrollment invite sent by email, so a stolen password alone cannot enroll.

**Related fix:** `/auth/2fa/validate` used to issue tokens without the `ver` claim. Once a staff member logged out (`token_version` ≥ 1), every later 2FA login produced tokens the server rejected. All token issuance now goes through `app.auth.issue_tokens`.

## Encryption key for TOTP secrets

Secrets are encrypted with a Fernet key derived (SHA-256) from `DATA_ENCRYPTION_KEY`, or from `JWT_SECRET_KEY` when that is empty. Set `DATA_ENCRYPTION_KEY` explicitly, so rotating the JWT secret does not make every enrolled authenticator unreadable. Known limitation: the key is derived from a passphrase and there is no key rotation (`MultiFernet`); both belong with a key-management service in production.

## Consequences

- Officers need an authenticator app. Seeded staff accounts start without 2FA and are taken through enrollment on their first login.
- Token version revocation ([ADR-005](005-jwt-auth.md)) and MFA together mean a logged-out officer's token is immediately dead — two independent layers of session security.
- Every 2FA sign-in is audited (`login_succeeded` with method `totp` / `totp_enrollment`, `login_failed` with reason `wrong_totp`), along with `2fa_enabled` / `2fa_disabled`.

## Production path

Add WebAuthn (passkey) support as a higher-assurance second factor. Keep TOTP as the fallback. Move `totp_secret` encryption to a KMS-managed key (AWS KMS or Vault) with `MultiFernet`-style rotation.
