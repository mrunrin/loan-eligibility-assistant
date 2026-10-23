# ADR-016 — Local Disk for Document Uploads

**Status:** Accepted (demo trade-off)  
**Date:** 2026-10-21

## Context

Borrowers upload supporting documents (ITR, bank statements, ID proof) as part of the loan application. These files must be stored and retrievable by officers. See [ADR-000](000-constraints.md): ₹0 budget, Windows laptop.

## Decision

Store uploaded files on **local disk** under `UPLOAD_DIR/{application_id}/{random hex}_{original name}`; the original file name, document type and SHA-256 are kept in the `documents` table. The random prefix means two files with the same name never overwrite each other. Files are served back through FastAPI using `FileResponse`. In Docker the directory is the named volume `uploads`, so files survive container restarts.

## Why not S3 or cloud storage

AWS S3, Google Cloud Storage, and Azure Blob all require a cloud account. Even free tiers require a credit card to sign up. The constraint is ₹0 and offline-capable for a demo. Local disk satisfies both.

## Security measures

Even with local storage, path traversal and file type attacks are mitigated:

1. **Filename sanitisation** — `pathlib.Path(filename).name` strips any directory components (`../../etc/passwd` → `passwd`). The `application_id` is a UUID (not user-supplied) and is used as the subdirectory.
2. **File type whitelist** — only `.pdf`, `.jpg`/`.jpeg` and `.png`, and the file's first bytes must match the extension (magic bytes `%PDF-`, JPEG and PNG signatures), so a renamed executable is rejected with 415.
3. **Size limit** — 10 MB per file, enforced while streaming to disk in 64 KB chunks; a failed upload removes the partial file.
4. **Access control** — uploads only by the owner while the application is a draft. Downloads check that the requesting user is the applicant or an officer/admin allowed to see the application, and that the document belongs to that application (no IDOR). Stored paths are resolved and must stay inside the upload root.
5. **Integrity** — the SHA-256 computed during upload is re-checked on every download; a mismatch returns 409 and writes a `document_integrity_failed` audit event.
6. **Audit** — every upload and download is recorded in `audit_events` (`document_uploaded`, `document_downloaded`, with the viewer's role).
7. **Required documents** — submit is refused until the product's checklist (`REQUIRED_DOCUMENTS` in `app/routers/applications.py`) is uploaded.

## Known limitations (documented, not hidden)

1. Files are stored on the same host as the application — no replication across instances.
2. Files are not encrypted at rest. In production: S3 with SSE-KMS.
3. A redeployed container with a new volume loses files unless the named volume is preserved.
4. No virus scanning (ClamAV) and image metadata (EXIF) is not stripped.
5. No retention/deletion schedule yet (DPDP); applications with documents cannot be deleted by the borrower.

## Consequences

- `uploads/` contents are in `.gitignore`; a `.gitkeep` keeps the directory in the repository.
- `docker-compose.yml` mounts the named volume `uploads` at `/app/uploads` in the backend (and the seed job).

## Production path

S3-compatible object storage (MinIO locally) with server-side encryption, and short-lived pre-signed URLs instead of `FileResponse`. File validation, access control, hashing and auditing stay in FastAPI; only the storage calls change. Add ClamAV scanning and image re-encoding in a background worker.
