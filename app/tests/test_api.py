"""
API integration tests against a real PostgreSQL (schema at alembic head).

Covers the authorization, state-machine, upload and token rules found in review.
Skipped automatically when the database is not reachable.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pyotp
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth import create_access_token, create_refresh_token, encrypt_secret, hash_password
from app.config import DATABASE_URL, MAX_FAILED_LOGINS


def _db_available() -> bool:
    try:
        import psycopg2

        psycopg2.connect(DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"), connect_timeout=2).close()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL not reachable"),
]

PASSWORD = "Str0ng!Passw0rd"
PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
FULL_APP = {
    "loan_amount_requested": 1_000_000,
    "loan_purpose": "expansion",
    "business_name": "Test Traders",
    "business_type": "LLP",
    "years_in_business": 3,
    "annual_revenue": 3_000_000,
    "monthly_emi_capacity": 100_000,
    "credit_score": 720,
}


class Ctx:
    def __init__(self, client: httpx.AsyncClient, session_factory):
        self.client = client
        self.Session = session_factory
        self.created: list[uuid.UUID] = []

    async def user(self, role: str = "borrower", **fields):
        from app.models import User

        user = User(
            email=f"t-{uuid.uuid4().hex[:10]}@example.com",
            hashed_password=hash_password(PASSWORD),
            full_name="Test User",
            role=role,
            **{"is_email_verified": True, **fields},
        )
        async with self.Session() as s:
            s.add(user)
            await s.commit()
        self.created.append(user.id)
        token = create_access_token({"sub": str(user.id), "role": role})
        return user, {"Authorization": f"Bearer {token}"}

    async def upload_required(self, app_id: str, headers: dict) -> None:
        required = (await self.client.get(f"/applications/{app_id}", headers=headers)).json()["required_documents"]
        for doc_type in required:
            r = await self.client.post(f"/applications/{app_id}/documents", params={"document_type": doc_type},
                                       files={"file": ("doc.pdf", PDF_BYTES)}, headers=headers)
            assert r.status_code == 201, r.text

    async def submitted_app(self, headers: dict, **overrides) -> str:
        r = await self.client.post("/applications", json={**FULL_APP, **overrides}, headers=headers)
        assert r.status_code == 201, r.text
        app_id = r.json()["id"]
        await self.upload_required(app_id, headers)
        r = await self.client.post(f"/applications/{app_id}/submit", headers=headers)
        assert r.status_code == 200, r.text
        return app_id


@pytest.fixture
async def ctx():
    from app.database import get_db
    from app.limiter import limiter
    from app.main import app
    from app.models import User

    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db
    limiter.enabled = False
    from app.routers.auth import ip_failures
    ip_failures.clear()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        context = Ctx(client, session_factory)
        yield context
    async with session_factory() as s:
        await s.execute(delete(User).where(User.id.in_(context.created)))
        await s.commit()
    app.dependency_overrides.clear()
    limiter.enabled = True
    await engine.dispose()


# ---------------------------------------------------------------------------
# Applications: ownership, validation, state machine
# ---------------------------------------------------------------------------


async def test_borrower_cannot_read_another_borrowers_application(ctx):
    _, owner = await ctx.user()
    _, other = await ctx.user()
    app_id = (await ctx.client.post("/applications", json=FULL_APP, headers=owner)).json()["id"]
    assert (await ctx.client.get(f"/applications/{app_id}", headers=other)).status_code == 403
    assert (await ctx.client.get(f"/applications/{app_id}/documents", headers=other)).status_code == 403
    assert (await ctx.client.get(f"/prequal/{app_id}", headers=other)).status_code == 403


async def test_malformed_application_id_is_422_not_500(ctx):
    _, headers = await ctx.user()
    assert (await ctx.client.get("/applications/not-a-uuid", headers=headers)).status_code == 422


async def test_patch_can_clear_a_field(ctx):
    _, headers = await ctx.user()
    app_id = (await ctx.client.post("/applications", json=FULL_APP, headers=headers)).json()["id"]
    r = await ctx.client.patch(f"/applications/{app_id}", json={"loan_purpose": None}, headers=headers)
    assert r.status_code == 200 and r.json()["loan_purpose"] is None
    assert r.json()["business_name"] == FULL_APP["business_name"]  # untouched fields stay


async def test_preferences_are_validated_stored_and_never_scored(ctx):
    _, headers = await ctx.user()
    prefs = {"facility_type": "dropline_od", "tenure_months": 36, "balance_transfer": True, "terms_accepted": True}
    r = await ctx.client.post("/applications", json={**FULL_APP, "preferences": prefs}, headers=headers)
    assert r.status_code == 201 and r.json()["preferences"] == prefs  # unanswered choices are not stored as nulls
    for bad in ({"facility_type": "mortgage"}, {"tenure_months": 120}, {"approve_me": True}):
        r = await ctx.client.post("/applications", json={**FULL_APP, "preferences": bad}, headers=headers)
        assert r.status_code == 422, bad
    with_prefs = await ctx.submitted_app(headers, preferences=prefs)
    without = await ctx.submitted_app(headers)
    scores = [(await ctx.client.get(f"/prequal/{a}", headers=headers)).json()["score"] for a in (with_prefs, without)]
    assert scores[0] == scores[1]


INDIVIDUAL = {
    "employment_type": "salaried",
    "employer_name": "Infosys",
    "date_of_birth": "1994-05-10",
    "work_experience_years": 5,
    "current_job_years": 2,
    "monthly_income": 80_000,
    "existing_emis": 5_000,
    "credit_score": 720,
}


async def test_personal_loan_and_credit_card_submit(ctx):
    _, headers = await ctx.user()
    pl_fields = {**{k: None for k in FULL_APP}, **INDIVIDUAL, "product_type": "personal_loan", "loan_amount_requested": 500_000}
    pl = await ctx.submitted_app(headers, **pl_fields)
    r = await ctx.client.get(f"/applications/{pl}", headers=headers)
    assert r.json()["status"] == "pre_qual_pass" and r.json()["title"] == "Personal Loan · Infosys"

    r = await ctx.client.post("/applications", json={"product_type": "credit_card", **INDIVIDUAL,
                                                     "employment_type": "self_employed", "employer_name": None},
                              headers=headers)
    cc = r.json()["id"]
    await ctx.upload_required(cc, headers)
    r = await ctx.client.post(f"/applications/{cc}/submit", headers=headers)
    assert r.status_code == 200 and r.json()["decision"] == "pass"
    titles = {a["title"] for a in (await ctx.client.get("/applications", headers=headers)).json()}
    assert titles == {"Personal Loan · Infosys", "Credit Card"}


async def test_product_specific_required_fields_and_validation(ctx):
    _, headers = await ctx.user()
    r = await ctx.client.post("/applications", json={"product_type": "personal_loan", "credit_score": 700}, headers=headers)
    r = await ctx.client.post(f"/applications/{r.json()['id']}/submit", headers=headers)
    assert r.status_code == 422 and "employer_name" in r.text and "business_name" not in r.text
    r = await ctx.client.post("/applications", json={"product_type": "car_loan"}, headers=headers)
    assert r.status_code == 422
    r = await ctx.client.post("/applications", json={"employment_type": "freelancer"}, headers=headers)
    assert r.status_code == 422
    app_id = (await ctx.client.post("/applications", json=FULL_APP, headers=headers)).json()["id"]
    r = await ctx.client.patch(f"/applications/{app_id}", json={"product_type": None}, headers=headers)
    assert r.status_code == 422


async def test_submit_with_missing_fields_is_422(ctx):
    _, headers = await ctx.user()
    app_id = (await ctx.client.post("/applications", json={"business_name": "Half"}, headers=headers)).json()["id"]
    r = await ctx.client.post(f"/applications/{app_id}/submit", headers=headers)
    assert r.status_code == 422 and "loan_amount_requested" in r.text


async def test_max_loan_rule_can_fail_through_the_api(ctx):
    _, headers = await ctx.user()
    app_id = await ctx.submitted_app(headers, loan_amount_requested=60_000_000, annual_revenue=200_000_000,
                                     monthly_emi_capacity=5_000_000)
    pq = (await ctx.client.get(f"/prequal/{app_id}", headers=headers)).json()
    max_rule = next(r for r in pq["rule_breakdown"] if r["rule"] == "Maximum loan amount")
    assert max_rule["passed"] is False


async def test_submit_requires_the_document_checklist(ctx):
    _, headers = await ctx.user()
    app_id = (await ctx.client.post("/applications", json=FULL_APP, headers=headers)).json()["id"]
    r = await ctx.client.post(f"/applications/{app_id}/submit", headers=headers)
    assert r.status_code == 422 and "Photo ID" in r.text
    await ctx.upload_required(app_id, headers)
    assert (await ctx.client.post(f"/applications/{app_id}/submit", headers=headers)).status_code == 200


async def test_uploaded_document_is_hashed_and_tampering_is_detected(ctx):
    from app.models import Document

    _, headers = await ctx.user()
    app_id = (await ctx.client.post("/applications", json=FULL_APP, headers=headers)).json()["id"]
    r = await ctx.client.post(f"/applications/{app_id}/documents", files={"file": ("a.pdf", PDF_BYTES)}, headers=headers)
    doc_id = r.json()["id"]
    docs = (await ctx.client.get(f"/applications/{app_id}/documents", headers=headers)).json()
    assert docs[0]["sha256"] == hashlib.sha256(PDF_BYTES).hexdigest()
    async with ctx.Session() as s:
        path = (await s.get(Document, uuid.UUID(doc_id))).file_path
    with open(path, "ab") as f:
        f.write(b"tampered")
    r = await ctx.client.get(f"/applications/{app_id}/documents/{doc_id}/download", headers=headers)
    assert r.status_code == 409


async def test_withdraw_is_a_post_and_blocked_after_a_decision(ctx):
    _, borrower = await ctx.user()
    app_id = await ctx.submitted_app(borrower)
    r = await ctx.client.post(f"/applications/{app_id}/withdraw", headers=borrower)
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"
    assert (await ctx.client.get(f"/applications/{app_id}", headers=borrower)).json()["status"] == "withdrawn"

    decided = await ctx.submitted_app(borrower)
    _, officer = await ctx.user("officer")
    await ctx.client.post(f"/officer/{decided}/claim", headers=officer)
    await ctx.client.post(f"/officer/{decided}/decision", json={"decision": "approved", "notes": "Documents verified."},
                          headers=officer)
    assert (await ctx.client.post(f"/applications/{decided}/withdraw", headers=borrower)).status_code == 409


async def test_failed_prequal_can_be_appealed_into_manual_review(ctx):
    _, borrower = await ctx.user()
    app_id = await ctx.submitted_app(borrower, credit_score=500)  # below the 550 floor → knockout fail
    assert (await ctx.client.get(f"/applications/{app_id}", headers=borrower)).json()["status"] == "pre_qual_fail"
    short = await ctx.client.post(f"/applications/{app_id}/appeal", json={"reason": "too short"}, headers=borrower)
    assert short.status_code == 422
    reason = "My bureau report shows 720; the score I typed was wrong."
    r = await ctx.client.post(f"/applications/{app_id}/appeal", json={"reason": reason}, headers=borrower)
    assert r.status_code == 200 and r.json()["status"] == "manual_review"
    _, officer = await ctx.user("officer")
    seen = (await ctx.client.get(f"/applications/{app_id}", headers=officer)).json()
    assert seen["appeal_reason"] == reason
    # A second appeal is refused: the application is no longer in pre_qual_fail.
    assert (await ctx.client.post(f"/applications/{app_id}/appeal", json={"reason": reason}, headers=borrower)).status_code == 409


async def test_double_submit_is_409(ctx):
    _, headers = await ctx.user()
    app_id = await ctx.submitted_app(headers)
    assert (await ctx.client.post(f"/applications/{app_id}/submit", headers=headers)).status_code == 409


# ---------------------------------------------------------------------------
# Officer workflow
# ---------------------------------------------------------------------------


async def test_prequal_rerun_twice_is_idempotent_not_500(ctx):
    _, borrower = await ctx.user()
    _, officer = await ctx.user("officer")
    app_id = await ctx.submitted_app(borrower)
    for _ in range(2):
        r = await ctx.client.post(f"/prequal/{app_id}/run", headers=officer)
        assert r.status_code == 200, r.text
    from app.models import PreQualificationResult

    async with ctx.Session() as s:
        rows = (await s.execute(
            select(PreQualificationResult).where(PreQualificationResult.application_id == uuid.UUID(app_id))
        )).scalars().all()
    assert len(rows) == 1


async def test_case_summary_is_officer_only_ai_first_with_rules_fallback(ctx, monkeypatch):
    from app import case_summary

    _, borrower = await ctx.user()
    _, officer = await ctx.user("officer")
    _, admin = await ctx.user("admin")
    app_id = await ctx.submitted_app(borrower)
    url = f"/prequal/{app_id}/summary"
    assert (await ctx.client.get(url, headers=borrower)).status_code == 403
    assert (await ctx.client.get(url, headers=admin)).status_code == 403  # admins keep the plain rule breakdown

    # Groq unavailable → plain rules summary, not cached.
    monkeypatch.setattr(case_summary, "provider_ready", lambda: (False, "no key"))
    body = (await ctx.client.get(url, headers=officer)).json()
    assert body["ai"] is False and 1 <= len(body["lines"]) <= case_summary.MAX_LINES

    # Groq available → one call, bullets parsed, and the second request is served from the cache.
    calls = []

    async def fake_groq(messages, **_):
        calls.append(messages[0]["content"])
        return "- Line one\n- Line two\n* Line three"

    monkeypatch.setattr(case_summary, "provider_ready", lambda: (True, ""))
    monkeypatch.setattr(case_summary, "groq_chat", fake_groq)
    for _ in range(2):
        body = (await ctx.client.get(url, headers=officer)).json()
        assert body == {"ai": True, "lines": ["Line one", "Line two", "Line three"]}
    assert len(calls) == 1


async def test_prequal_rerun_cannot_reopen_a_decided_or_draft_application(ctx):
    _, borrower = await ctx.user()
    _, officer = await ctx.user("officer")
    app_id = await ctx.submitted_app(borrower)
    assert (await ctx.client.post(f"/officer/{app_id}/claim", headers=officer)).status_code == 200
    assert (await ctx.client.post(f"/officer/{app_id}/decision", json={"decision": "approved", "notes": "Documents verified, income proof OK"}, headers=officer)).status_code == 200
    assert (await ctx.client.post(f"/prequal/{app_id}/run", headers=officer)).status_code == 409
    draft_id = (await ctx.client.post("/applications", json=FULL_APP, headers=borrower)).json()["id"]
    assert (await ctx.client.post(f"/prequal/{draft_id}/run", headers=officer)).status_code == 409


async def test_officer_cannot_decide_own_application(ctx):
    _, officer = await ctx.user("officer")
    app_id = await ctx.submitted_app(officer)
    r = await ctx.client.post(f"/officer/{app_id}/decision", json={"decision": "approved", "notes": "Documents verified, income proof OK"}, headers=officer)
    assert r.status_code == 403


async def test_officer_cannot_decide_a_draft(ctx):
    _, borrower = await ctx.user()
    _, officer = await ctx.user("officer")
    draft_id = (await ctx.client.post("/applications", json=FULL_APP, headers=borrower)).json()["id"]
    r = await ctx.client.post(f"/officer/{draft_id}/decision", json={"decision": "approved", "notes": "Documents verified, income proof OK"}, headers=officer)
    assert r.status_code == 409


async def test_claim_moves_to_under_review_and_blocks_other_officers(ctx):
    _, borrower = await ctx.user()
    _, officer_a = await ctx.user("officer")
    _, officer_b = await ctx.user("officer")
    app_id = await ctx.submitted_app(borrower)
    r = await ctx.client.post(f"/officer/{app_id}/claim", headers=officer_a)
    assert r.status_code == 200 and r.json()["status"] == "under_review"
    r = await ctx.client.post(f"/officer/{app_id}/decision", json={"decision": "rejected", "notes": "Income proof could not be verified"}, headers=officer_b)
    assert r.status_code == 409
    r = await ctx.client.post(f"/officer/{app_id}/decision", json={"decision": "rejected", "notes": "Income proof could not be verified"}, headers=officer_a)
    assert r.status_code == 200


async def test_every_decision_requires_a_reason_and_is_audited(ctx):
    from app.models import AuditEvent, LoanApplication

    _, borrower = await ctx.user()
    officer, officer_h = await ctx.user("officer")
    app_id = await ctx.submitted_app(borrower)
    await ctx.client.post(f"/officer/{app_id}/claim", headers=officer_h)
    for body in ({"decision": "approved"}, {"decision": "approved", "notes": "   ok   "},
                 {"decision": "rejected", "notes": ""}):
        assert (await ctx.client.post(f"/officer/{app_id}/decision", json=body, headers=officer_h)).status_code == 422
    async with ctx.Session() as s:
        assert (await s.get(LoanApplication, uuid.UUID(app_id))).status == "under_review"

    reason = "  Salary slips and bank statements match declared income  "
    r = await ctx.client.post(f"/officer/{app_id}/decision", json={"decision": "approved", "notes": reason}, headers=officer_h)
    assert r.status_code == 200
    async with ctx.Session() as s:
        app = await s.get(LoanApplication, uuid.UUID(app_id))
        assert (app.review_notes, app.reviewed_by) == (reason.strip(), officer.id)
        event = (await s.execute(
            select(AuditEvent).where(AuditEvent.event_type == "application_decision", AuditEvent.user_id == officer.id)
        )).scalar_one()
        assert event.payload == {"application_id": app_id, "decision": "approved", "reason": reason.strip()}


async def test_officer_decision_history_is_own_and_filterable(ctx):
    _, borrower = await ctx.user()
    _, officer_a = await ctx.user("officer")
    _, officer_b = await ctx.user("officer")
    approved_id = await ctx.submitted_app(borrower)
    rejected_id = await ctx.submitted_app(borrower)
    for app_id, decision in ((approved_id, "approved"), (rejected_id, "rejected")):
        await ctx.client.post(f"/officer/{app_id}/claim", headers=officer_a)
        await ctx.client.post(f"/officer/{app_id}/decision", json={"decision": decision, "notes": f"Reason for {decision}"},
                              headers=officer_a)

    approved = (await ctx.client.get("/officer/decisions", params={"decision": "approved"}, headers=officer_a)).json()
    assert [(a["id"], a["review_notes"]) for a in approved] == [(approved_id, "Reason for approved")]
    rejected = (await ctx.client.get("/officer/decisions", params={"decision": "rejected"}, headers=officer_a)).json()
    assert [a["id"] for a in rejected] == [rejected_id]
    assert len((await ctx.client.get("/officer/decisions", headers=officer_a)).json()) == 2
    assert (await ctx.client.get("/officer/decisions", headers=officer_b)).json() == []
    # The officer can still open what they decided; the other officer cannot.
    detail = (await ctx.client.get(f"/applications/{rejected_id}", headers=officer_a)).json()
    assert detail["review_notes"] == "Reason for rejected" and detail["reviewed_at"]
    assert (await ctx.client.get(f"/applications/{rejected_id}", headers=officer_b)).status_code == 403


async def test_claimed_application_is_hidden_from_other_officers(ctx):
    _, borrower = await ctx.user()
    _, officer_a = await ctx.user("officer")
    _, officer_b = await ctx.user("officer")
    _, admin = await ctx.user("admin")
    app_id = await ctx.submitted_app(borrower)

    def ids(resp):
        return {a["id"] for a in resp.json()}

    # Unclaimed: every officer sees it.
    assert app_id in ids(await ctx.client.get("/officer/queue", params={"limit": 100}, headers=officer_b))
    assert (await ctx.client.post(f"/officer/{app_id}/claim", headers=officer_a)).status_code == 200

    queue_a = (await ctx.client.get("/officer/queue", params={"limit": 100}, headers=officer_a)).json()
    mine = next(a for a in queue_a if a["id"] == app_id)
    assert mine["assigned_to_me"] is True and queue_a[0]["status"] == "under_review"  # own reviews listed first

    # Claimed by A: B cannot find or open it anywhere.
    assert app_id not in ids(await ctx.client.get("/officer/queue", params={"limit": 100}, headers=officer_b))
    assert app_id not in ids(await ctx.client.get("/applications", params={"limit": 100}, headers=officer_b))
    for path in (f"/applications/{app_id}", f"/applications/{app_id}/documents", f"/prequal/{app_id}"):
        assert (await ctx.client.get(path, headers=officer_b)).status_code == 403, path

    # A and admins still can.
    assert (await ctx.client.get(f"/applications/{app_id}", headers=officer_a)).status_code == 200
    assert (await ctx.client.get(f"/applications/{app_id}", headers=admin)).status_code == 200
    admin_queue = (await ctx.client.get("/officer/queue", params={"limit": 100}, headers=admin)).json()
    assert any(a["id"] == app_id and a["assigned_to_me"] is False for a in admin_queue)


async def test_borrower_cannot_use_officer_endpoints(ctx):
    _, borrower = await ctx.user()
    assert (await ctx.client.get("/officer/queue", headers=borrower)).status_code == 403
    assert (await ctx.client.get("/admin/users", headers=borrower)).status_code == 403


async def test_admin_self_demotion_blocked_even_with_uppercase_uuid(ctx):
    admin, headers = await ctx.user("admin")
    r = await ctx.client.post(f"/admin/users/{str(admin.id).upper()}/role", json={"role": "borrower"}, headers=headers)
    assert r.status_code == 400


@pytest.mark.parametrize("role", ["borrower", "officer"])
async def test_admin_deactivation_blocks_access_until_reactivated(ctx, role):
    _, admin = await ctx.user("admin")
    target, target_headers = await ctx.user(role)
    r = await ctx.client.post(f"/admin/users/{target.id}/active", headers=admin)
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert (await ctx.client.get("/auth/me", headers=target_headers)).status_code == 401
    r = await ctx.client.post(f"/admin/users/{target.id}/active", headers=admin)
    assert r.status_code == 200 and r.json()["is_active"] is True
    assert (await ctx.client.get("/auth/me", headers=target_headers)).status_code == 200


async def test_admin_cannot_deactivate_self_or_another_admin(ctx):
    from app.models import User

    me, admin = await ctx.user("admin")
    other, other_headers = await ctx.user("admin")
    assert (await ctx.client.post(f"/admin/users/{me.id}/active", headers=admin)).status_code == 400
    assert (await ctx.client.post(f"/admin/users/{other.id}/active", headers=admin)).status_code == 403
    assert (await ctx.client.get("/auth/me", headers=other_headers)).status_code == 200
    # An admin who is already inactive can still be let back in.
    inactive, _ = await ctx.user("admin", is_active=False)
    r = await ctx.client.post(f"/admin/users/{inactive.id}/active", headers=admin)
    assert r.status_code == 200 and r.json()["is_active"] is True
    async with ctx.Session() as s:
        assert (await s.get(User, other.id)).is_active is True


async def test_admin_cannot_demote_another_admin_but_can_promote(ctx):
    from app.models import User

    _, admin = await ctx.user("admin")
    other, _ = await ctx.user("admin")
    for role in ("borrower", "officer"):
        r = await ctx.client.post(f"/admin/users/{other.id}/role", json={"role": role}, headers=admin)
        assert r.status_code == 403
    officer, _ = await ctx.user("officer")
    r = await ctx.client.post(f"/admin/users/{officer.id}/role", json={"role": "admin"}, headers=admin)
    assert r.status_code == 200 and r.json()["role"] == "admin"
    async with ctx.Session() as s:
        assert (await s.get(User, other.id)).role == "admin"


async def test_pagination_limit_is_capped(ctx):
    _, headers = await ctx.user("admin")
    assert (await ctx.client.get("/admin/users", params={"limit": 10_000}, headers=headers)).status_code == 422


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


async def test_upload_checks_content_and_download_is_authorised(ctx):
    _, borrower = await ctx.user()
    _, stranger = await ctx.user()
    _, officer = await ctx.user("officer")
    app_id = (await ctx.client.post("/applications", json=FULL_APP, headers=borrower)).json()["id"]

    fake = await ctx.client.post(f"/applications/{app_id}/documents", files={"file": ("x.pdf", b"MZ\x90\x00binary")}, headers=borrower)
    assert fake.status_code == 415
    bad_type = await ctx.client.post(
        f"/applications/{app_id}/documents", params={"document_type": "Anything"},
        files={"file": ("x.pdf", PDF_BYTES)}, headers=borrower,
    )
    assert bad_type.status_code == 422
    traversal = await ctx.client.post(
        f"/applications/{app_id}/documents", params={"document_type": "ITR"},
        files={"file": ("../../evil.pdf", PDF_BYTES)}, headers=borrower,
    )
    assert traversal.status_code == 201 and traversal.json()["file_name"] == "evil.pdf"
    doc_id = traversal.json()["id"]

    url = f"/applications/{app_id}/documents/{doc_id}/download"
    assert (await ctx.client.get(url, headers=stranger)).status_code == 403
    officer_dl = await ctx.client.get(url, headers=officer)
    assert officer_dl.status_code == 200 and officer_dl.content == PDF_BYTES


async def test_upload_size_limit_and_draft_only(ctx):
    _, borrower = await ctx.user()
    app_id = (await ctx.client.post("/applications", json=FULL_APP, headers=borrower)).json()["id"]
    too_big = b"%PDF-" + b"0" * (10 * 1024 * 1024)
    r = await ctx.client.post(f"/applications/{app_id}/documents", files={"file": ("big.pdf", too_big)}, headers=borrower)
    assert r.status_code == 413
    await ctx.upload_required(app_id, borrower)
    submit = await ctx.client.post(f"/applications/{app_id}/submit", headers=borrower)
    assert submit.status_code == 200, submit.text
    r = await ctx.client.post(f"/applications/{app_id}/documents", files={"file": ("late.pdf", PDF_BYTES)}, headers=borrower)
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Auth & MFA
# ---------------------------------------------------------------------------


async def test_refresh_token_is_not_an_access_token(ctx):
    user, _ = await ctx.user()
    refresh = create_refresh_token({"sub": str(user.id), "role": "borrower"})
    r = await ctx.client.get("/auth/me", headers={"Authorization": f"Bearer {refresh}"})
    assert r.status_code == 401


async def test_refresh_reuse_revokes_the_session(ctx):
    user, _ = await ctx.user()
    login = await ctx.client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    first = login.json()["refresh_token"]
    rotated = await ctx.client.post("/auth/refresh", json={"refresh_token": first})
    assert rotated.status_code == 200
    second = rotated.json()["refresh_token"]
    assert (await ctx.client.post("/auth/refresh", json={"refresh_token": first})).status_code == 401
    # The stolen-token replay also kills the legitimate holder's current token.
    assert (await ctx.client.post("/auth/refresh", json={"refresh_token": second})).status_code == 401


async def test_login_locks_after_repeated_failures(ctx):
    user, _ = await ctx.user()
    for _ in range(MAX_FAILED_LOGINS):
        r = await ctx.client.post("/auth/login", json={"email": user.email, "password": "Wrong!Passw0rd"})
        assert r.status_code == 401
    r = await ctx.client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 401  # correct password, but locked


async def test_login_error_is_identical_for_unknown_email(ctx):
    user, _ = await ctx.user()
    unknown = await ctx.client.post("/auth/login", json={"email": "nobody-" + user.email, "password": PASSWORD})
    wrong = await ctx.client.post("/auth/login", json={"email": user.email, "password": "Wrong!Passw0rd"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


async def test_repeat_lockout_doubles_and_success_resets_backoff(ctx):
    from app.config import LOCKOUT_MINUTES
    from app.models import User

    user, _ = await ctx.user(lockout_count=1)
    for _ in range(MAX_FAILED_LOGINS):
        await ctx.client.post("/auth/login", json={"email": user.email, "password": "Wrong!Passw0rd"})
    async with ctx.Session() as s:
        locked = await s.get(User, user.id)
        minutes = (locked.locked_until - datetime.now(timezone.utc)).total_seconds() / 60
        assert locked.lockout_count == 2
        assert LOCKOUT_MINUTES * 2 - 1 < minutes <= LOCKOUT_MINUTES * 2  # second lockout: doubled

    fresh, _ = await ctx.user(lockout_count=3)
    assert (await ctx.client.post("/auth/login", json={"email": fresh.email, "password": PASSWORD})).status_code == 200
    async with ctx.Session() as s:
        assert (await s.get(User, fresh.id)).lockout_count == 0


async def test_one_ip_spraying_many_accounts_gets_429_without_locking_them(ctx):
    from app.config import IP_FAILED_LOGIN_LIMIT
    from app.models import User

    victims = [(await ctx.user())[0] for _ in range(IP_FAILED_LOGIN_LIMIT // (MAX_FAILED_LOGINS - 1) + 1)]
    attempts = 0
    for victim in victims:
        for _ in range(MAX_FAILED_LOGINS - 1):  # stay under each account's own limit
            r = await ctx.client.post("/auth/login", json={"email": victim.email, "password": "Wrong!Passw0rd"})
            attempts += 1
            assert r.status_code == (429 if attempts > IP_FAILED_LOGIN_LIMIT else 401)
    async with ctx.Session() as s:
        assert all([(await s.get(User, v.id)).locked_until is None for v in victims])  # list: await in a genexp makes an async generator


async def test_idle_or_overlong_session_is_rejected_server_side(ctx):
    from app.config import SESSION_IDLE_MINUTES, SESSION_MAX_HOURS

    now = datetime.now(timezone.utc)
    _, idle = await ctx.user(last_seen_at=now - timedelta(minutes=SESSION_IDLE_MINUTES + 1))
    assert (await ctx.client.get("/auth/me", headers=idle)).status_code == 401
    _, old = await ctx.user(last_seen_at=now, session_started_at=now - timedelta(hours=SESSION_MAX_HOURS, minutes=1))
    assert (await ctx.client.get("/auth/me", headers=old)).status_code == 401
    _, active = await ctx.user(last_seen_at=now, session_started_at=now)
    assert (await ctx.client.get("/auth/me", headers=active)).status_code == 200


async def test_refresh_fails_after_idle_timeout(ctx):
    from app.config import SESSION_IDLE_MINUTES
    from app.models import User

    user, _ = await ctx.user()
    login = await ctx.client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
    async with ctx.Session() as s:
        row = await s.get(User, user.id)
        row.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=SESSION_IDLE_MINUTES + 1)
        await s.commit()
    r = await ctx.client.post("/auth/refresh", json={"refresh_token": login.json()["refresh_token"]})
    assert r.status_code == 401


async def test_token_for_another_audience_is_rejected(ctx):
    from jose import jwt

    from app.config import JWT_ALGORITHM, JWT_SECRET_KEY

    user, _ = await ctx.user()
    foreign = jwt.encode({"sub": str(user.id), "type": "access", "ver": 0, "aud": "some-other-service"},
                         JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    assert (await ctx.client.get("/auth/me", headers={"Authorization": f"Bearer {foreign}"})).status_code == 401


async def test_dev_verify_is_404_when_disabled(ctx):
    from app.config import DEV_EMAIL_VERIFY

    assert not DEV_EMAIL_VERIFY  # production default
    user, _ = await ctx.user(is_email_verified=False)
    assert (await ctx.client.post("/auth/dev-verify", json={"email": user.email})).status_code == 404


async def test_logins_and_document_access_are_audited(ctx):
    from app.models import AuditEvent

    user, headers = await ctx.user()
    await ctx.client.post("/auth/login", json={"email": user.email, "password": "Wrong!Passw0rd"})
    await ctx.client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
    await ctx.client.post("/auth/login", json={"email": "nobody-" + user.email, "password": PASSWORD})
    app_id = (await ctx.client.post("/applications", json=FULL_APP, headers=headers)).json()["id"]
    doc_id = (await ctx.client.post(f"/applications/{app_id}/documents", files={"file": ("a.pdf", PDF_BYTES)},
                                    headers=headers)).json()["id"]
    await ctx.client.get(f"/applications/{app_id}/documents/{doc_id}/download", headers=headers)
    async with ctx.Session() as s:
        mine = (await s.execute(select(AuditEvent).where(AuditEvent.user_id == user.id))).scalars().all()
        unknown = (await s.execute(select(AuditEvent).where(
            AuditEvent.event_type == "login_failed", AuditEvent.payload["reason"].astext == "unknown_email",
            AuditEvent.payload["email_sha256"].astext == hashlib.sha256(("nobody-" + user.email).encode()).hexdigest(),
        ))).scalars().all()
    events = [(e.event_type, (e.payload or {}).get("reason")) for e in mine]
    assert ("login_failed", "wrong_password") in events and ("login_succeeded", None) in events
    assert {"document_uploaded", "document_downloaded"} <= {e for e, _ in events}
    assert len(unknown) == 1 and user.email not in str(unknown[0].payload)


async def test_audit_events_are_append_only(ctx):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from app.models import AuditEvent

    user, _ = await ctx.user()
    await ctx.client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
    for statement in ("UPDATE audit_events SET event_type = 'edited' WHERE user_id = :u",
                      "DELETE FROM audit_events WHERE user_id = :u"):
        async with ctx.Session() as s:
            with pytest.raises(DBAPIError, match="append-only"):
                await s.execute(text(statement), {"u": user.id})
    # Deleting the account is still allowed: its events stay, detached from the user (FK SET NULL).
    async with ctx.Session() as s:
        await s.delete(await s.get(type(user), user.id))
        await s.commit()
        orphaned = (await s.execute(select(AuditEvent).where(
            AuditEvent.event_type == "login_succeeded", AuditEvent.user_id.is_(None),
        ))).scalars().all()
    assert orphaned


async def test_register_does_not_reveal_existing_email(ctx):
    user, _ = await ctx.user()
    body = {"email": user.email, "password": PASSWORD, "full_name": "Dup"}
    r = await ctx.client.post("/auth/register", json=body)
    assert r.status_code == 201 and r.json()["email"] == user.email


async def test_email_otp_burns_after_max_attempts(ctx):
    from app.auth import hmac_code
    from app.config import MAX_FAILED_OTP
    from app.models import User

    user, _ = await ctx.user(
        is_email_verified=False,
        email_verify_token_hash=hmac_code("123456"),
        email_verify_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    for _ in range(MAX_FAILED_OTP):
        assert (await ctx.client.post("/auth/verify-email", json={"email": user.email, "otp": "000000"})).status_code == 400
    # The right code no longer works: it was burned.
    assert (await ctx.client.post("/auth/verify-email", json={"email": user.email, "otp": "123456"})).status_code == 400
    async with ctx.Session() as s:
        assert (await s.get(User, user.id)).is_email_verified is False


async def test_2fa_setup_cannot_disable_existing_2fa(ctx):
    secret = pyotp.random_base32()
    user, headers = await ctx.user(totp_secret=encrypt_secret(secret), totp_enabled=True)
    assert (await ctx.client.post("/auth/2fa/setup", headers=headers)).status_code == 400
    from app.models import User

    async with ctx.Session() as s:
        assert (await s.get(User, user.id)).totp_enabled is True


async def test_totp_code_cannot_be_replayed(ctx):
    secret = pyotp.random_base32()
    user, _ = await ctx.user(totp_secret=encrypt_secret(secret), totp_enabled=True)
    login = await ctx.client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
    mfa_token = login.json()["mfa_token"]
    code = pyotp.TOTP(secret).now()
    first = await ctx.client.post("/auth/2fa/validate", json={"mfa_token": mfa_token, "otp_code": code})
    assert first.status_code == 200
    replay = await ctx.client.post("/auth/2fa/validate", json={"mfa_token": mfa_token, "otp_code": code})
    assert replay.status_code == 401


async def test_staff_first_login_enrolls_2fa_and_signs_in(ctx):
    officer, _ = await ctx.user(role="officer")
    login = await ctx.client.post("/auth/login", json={"email": officer.email, "password": PASSWORD})
    assert login.status_code == 200 and login.json()["mfa_enrollment_required"] is True
    assert "access_token" not in login.json()
    enroll_token = login.json()["enroll_token"]

    # The enrollment token is not a session: protected endpoints and the 2FA login step reject it.
    bearer = {"Authorization": f"Bearer {enroll_token}"}
    assert (await ctx.client.get("/auth/me", headers=bearer)).status_code == 401
    assert (await ctx.client.post("/auth/2fa/validate", json={"mfa_token": enroll_token, "otp_code": "123456"})).status_code == 401

    setup = await ctx.client.post("/auth/2fa/enroll/setup", json={"enroll_token": enroll_token})
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    wrong = await ctx.client.post("/auth/2fa/enroll/confirm", json={"enroll_token": enroll_token, "otp_code": "000000"})
    assert wrong.status_code == 400
    done = await ctx.client.post(
        "/auth/2fa/enroll/confirm", json={"enroll_token": enroll_token, "otp_code": pyotp.TOTP(secret).now()}
    )
    assert done.status_code == 200
    me = await ctx.client.get("/auth/me", headers={"Authorization": f"Bearer {done.json()['access_token']}"})
    assert me.status_code == 200 and me.json()["totp_enabled"] is True

    # Enrollment is one-time: the token cannot restart setup, and the next login asks for a code.
    assert (await ctx.client.post("/auth/2fa/enroll/setup", json={"enroll_token": enroll_token})).status_code == 400
    again = await ctx.client.post("/auth/login", json={"email": officer.email, "password": PASSWORD})
    assert again.json().get("mfa_required") is True


async def test_enroll_endpoints_reject_borrower_and_garbage_tokens(ctx):
    _, headers = await ctx.user()
    access = headers["Authorization"].removeprefix("Bearer ")
    for token in (access, "not-a-jwt"):
        assert (await ctx.client.post("/auth/2fa/enroll/setup", json={"enroll_token": token})).status_code == 401


async def test_2fa_login_after_logout_issues_usable_tokens(ctx):
    secret = pyotp.random_base32()
    user, _ = await ctx.user(totp_secret=encrypt_secret(secret), totp_enabled=True, token_version=3)
    mfa_token = (await ctx.client.post("/auth/login", json={"email": user.email, "password": PASSWORD})).json()["mfa_token"]
    tokens = await ctx.client.post("/auth/2fa/validate", json={"mfa_token": mfa_token, "otp_code": pyotp.TOTP(secret).now()})
    me = await ctx.client.get("/auth/me", headers={"Authorization": f"Bearer {tokens.json()['access_token']}"})
    assert me.status_code == 200


async def test_staff_cannot_disable_2fa(ctx):
    secret = pyotp.random_base32()
    _, headers = await ctx.user(role="officer", totp_secret=encrypt_secret(secret), totp_enabled=True)
    r = await ctx.client.post("/auth/2fa/disable", json={"otp_code": pyotp.TOTP(secret).now()}, headers=headers)
    assert r.status_code == 403


async def test_delete_account_requires_password(ctx):
    _, headers = await ctx.user()
    wrong = await ctx.client.request("DELETE", "/auth/me", json={"password": "Wrong!Passw0rd"}, headers=headers)
    assert wrong.status_code == 403
    right = await ctx.client.request("DELETE", "/auth/me", json={"password": PASSWORD}, headers=headers)
    assert right.status_code == 204


async def test_delete_account_blocked_when_applications_exist(ctx):
    _, headers = await ctx.user()
    await ctx.client.post("/applications", json=FULL_APP, headers=headers)
    r = await ctx.client.request("DELETE", "/auth/me", json={"password": PASSWORD}, headers=headers)
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------


async def test_chat_endpoints_require_authentication(ctx):
    body = {"question": "What is the EMI due date?", "session_id": "t"}
    assert (await ctx.client.post("/ask", json=body)).status_code == 401
    assert (await ctx.client.post("/ask/stream", json=body)).status_code == 401


async def test_request_id_header_is_set(ctx):
    r = await ctx.client.get("/", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"
    r = await ctx.client.get("/", headers={"X-Request-ID": "bad id with spaces"})
    assert r.headers["X-Request-ID"] != "bad id with spaces"
