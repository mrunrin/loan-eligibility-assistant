"""
Seed script for demo data.

Usage (with Postgres running):
  python scripts/seed_demo.py

Idempotent: skips users/apps that already exist.
Assumes alembic upgrade head has already been run.

Each user gets the password from its "password" field in seed_data/users.json. These are public
demo credentials: never reuse them for real accounts.
"""

import csv
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path so app.* imports work
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from passlib.context import CryptContext
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import DATABASE_URL

_pwd = CryptContext(schemes=["bcrypt"])

DATA = ROOT / "scripts" / "seed_data"

USERS_FILE = DATA / "users.json"
APPS_FILE = DATA / "applications.json"
MANIFEST_FILE = DATA / "document_manifest.csv"
UPLOADS_DIR = ROOT / "uploads"

ROLE_MAP = {"loan_officer": "officer", "borrower": "borrower", "admin": "admin"}


def _stub_pdf(path: Path) -> None:
    """Write a minimal valid single-page PDF stub."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    content = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF\n"
    )
    path.write_bytes(content)


def seed(db: Session) -> None:
    users_raw = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    apps_raw = json.loads(APPS_FILE.read_text(encoding="utf-8"))

    with open(MANIFEST_FILE, newline="", encoding="utf-8-sig") as f:
        docs_raw = list(csv.DictReader(f))

    # ── 1. Users ─────────────────────────────────────────────────────────────
    email_to_id: dict[str, uuid.UUID] = {}

    for u in users_raw:
        existing = db.execute(
            text("SELECT id FROM users WHERE lower(email) = :e"), {"e": u["email"].lower()}
        ).fetchone()
        if existing:
            email_to_id[u["email"]] = existing[0]
            # Databases seeded before demo users were pre-verified would otherwise stay locked out.
            db.execute(text("UPDATE users SET is_email_verified = true WHERE id = :id"), {"id": existing[0]})
            print(f"  skip user {u['email']} (exists; ensured verified)")
            continue

        uid = uuid.uuid4()
        role = ROLE_MAP.get(u["role"], "borrower")
        # Seeded demo accounts are pre-verified: login rejects unverified users.
        db.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, full_name, role, is_active, is_email_verified)"
                " VALUES (:id, :email, :pw, :name, :role, true, true)"
            ),
            {
                "id": uid,
                "email": u["email"].lower(),
                "pw": _pwd.hash(u["password"]),
                "name": u["full_name"],
                "role": role,
            },
        )
        email_to_id[u["email"]] = uid
        print(f"  created user {u['email']} ({role})")

    db.commit()

    # pick a default reviewer (first officer found)
    reviewer_id = None
    for u in users_raw:
        if u["role"] in ("loan_officer", "officer"):
            reviewer_id = email_to_id.get(u["email"])
            if reviewer_id:
                break

    # ── 2. Applications + pre-qual results ───────────────────────────────────
    app_ref_to_id: dict[str, uuid.UUID] = {}
    CLAIMED_STATUSES = {"under_review", "approved", "rejected"}
    DECIDED_STATUSES = {"approved", "rejected"}

    for a in apps_raw:
        applicant_id = email_to_id.get(a["applicant_email"])
        if not applicant_id:
            print(f"  WARN: no user for {a['applicant_email']}, skipping {a['application_ref']}")
            continue

        product = a.get("product_type", "business_loan")
        existing = db.execute(
            text(
                "SELECT id FROM loan_applications"
                " WHERE applicant_id = :uid AND product_type = :pt"
                "   AND business_name IS NOT DISTINCT FROM :bn"
                "   AND employer_name IS NOT DISTINCT FROM :en"
                "   AND loan_amount_requested IS NOT DISTINCT FROM :amt"
            ),
            {"uid": applicant_id, "pt": product, "bn": a.get("business_name"),
             "en": a.get("employer_name"), "amt": a.get("loan_amount_requested")},
        ).fetchone()
        if existing:
            app_ref_to_id[a["application_ref"]] = existing[0]
            print(f"  skip app {a['application_ref']} (exists)")
            continue

        app_id = uuid.uuid4()
        app_ref_to_id[a["application_ref"]] = app_id
        status = a["status"]

        db.execute(
            text(
                "INSERT INTO loan_applications"
                " (id, applicant_id, status, product_type, loan_amount_requested, loan_purpose,"
                "  business_name, business_type, years_in_business, annual_revenue,"
                "  monthly_emi_capacity, credit_score, employment_type, employer_name,"
                "  date_of_birth, work_experience_years, current_job_years, monthly_income,"
                "  existing_emis, reviewed_by, reviewed_at)"
                " VALUES (:id, :uid, :status, :pt, :amt, :purpose, :bname, :btype,"
                "         :years, :rev, :emi, :cs, :emp, :employer, :dob, :exp, :cur,"
                "         :income, :existing, :reviewer, :reviewed_at)"
            ),
            {
                "id": app_id,
                "uid": applicant_id,
                "status": status,
                "pt": product,
                "amt": a.get("loan_amount_requested"),
                "purpose": a.get("loan_purpose"),
                "bname": a.get("business_name"),
                "btype": a.get("business_type"),
                "years": a.get("years_in_business"),
                "rev": a.get("annual_revenue"),
                "emi": a.get("monthly_emi_capacity"),
                "cs": a.get("credit_score"),
                "emp": a.get("employment_type"),
                "employer": a.get("employer_name"),
                "dob": a.get("date_of_birth"),
                "exp": a.get("work_experience_years"),
                "cur": a.get("current_job_years"),
                "income": a.get("monthly_income"),
                "existing": a.get("existing_emis"),
                "reviewer": reviewer_id if status in CLAIMED_STATUSES else None,
                "reviewed_at": datetime.now(timezone.utc) if status in DECIDED_STATUSES else None,
            },
        )
        print(f"  created app {a['application_ref']} ({status})")

        pq = a.get("pre_qualification_result")
        if pq:
            # Normalise rule_breakdown keys: name→rule, score_contribution→score
            breakdown = []
            for r in pq.get("rule_breakdown", []):
                breakdown.append(
                    {
                        "rule": r.get("rule") or r.get("name", ""),
                        "passed": r["passed"],
                        "score": r.get("score") or r.get("score_contribution", 0),
                        "message": r.get("message", ""),
                        "knockout": r.get("knockout", False),
                    }
                )

            db.execute(
                text(
                    "INSERT INTO pre_qualification_results"
                    " (id, application_id, score, decision, rule_breakdown)"
                    " VALUES (:id, :app_id, :score, :decision, CAST(:breakdown AS jsonb))"
                ),
                {
                    "id": uuid.uuid4(),
                    "app_id": app_id,
                    "score": pq["score"],
                    "decision": pq["decision"],
                    "breakdown": json.dumps(breakdown),
                },
            )

    db.commit()

    # ── 3. Documents (stub files + DB rows) ───────────────────────────────────
    for row in docs_raw:
        ref = row["application_ref"]
        app_id = app_ref_to_id.get(ref)
        if not app_id:
            print(f"  WARN: no app_id for {ref}, skipping doc {row['filename']}")
            continue

        file_path = ROOT / row["relative_path"]
        _stub_pdf(file_path)

        existing = db.execute(
            text(
                "SELECT id FROM documents WHERE application_id = :aid AND file_name = :fn"
            ),
            {"aid": app_id, "fn": row["filename"]},
        ).fetchone()
        if existing:
            continue

        db.execute(
            text(
                "INSERT INTO documents (id, application_id, document_type, file_name, file_path)"
                " VALUES (:id, :aid, :dtype, :fname, :fpath)"
            ),
            {
                "id": uuid.uuid4(),
                "aid": app_id,
                "dtype": row["document_type"],
                "fname": row["filename"],
                "fpath": row["relative_path"],
            },
        )

    db.commit()
    print("\nSeed complete. Sign in with the emails and passwords in scripts/seed_data/users.json.")


if __name__ == "__main__":
    db_url = DATABASE_URL
    if not db_url:
        sys.exit("DATABASE_URL not set — check your .env file.")

    # Use sync driver for this script (psycopg2)
    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(sync_url)
    with Session(engine) as session:
        seed(session)
