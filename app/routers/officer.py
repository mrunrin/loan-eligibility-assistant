import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.audit import record_db_event
from app.database import get_db
from app.deps import require_role
from app.models import LoanApplication, User
from app.routers.applications import claim_expired, notify_applicant, officer_visible

router = APIRouter(prefix="/officer", tags=["officer"])

# pre_qual_pass / manual_review wait in the queue; an officer claims one (→ under_review) to work it.
CLAIMABLE_STATUSES = {"pre_qual_pass", "manual_review"}
REVIEWABLE_STATUSES = CLAIMABLE_STATUSES | {"under_review"}


MIN_REASON_CHARS = 10


class DecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    # Mandatory for both outcomes: the audit trail must say who decided and why (fraud / complaint traceback).
    notes: str = Field(..., max_length=5000)

    @field_validator("notes")
    @classmethod
    def reason_required(cls, v: str) -> str:
        v = v.strip()
        if len(v) < MIN_REASON_CHARS:
            raise ValueError(f"A reason of at least {MIN_REASON_CHARS} characters is required for every decision.")
        return v


async def _load_locked(db: AsyncSession, app_id: uuid.UUID) -> LoanApplication:
    app = (
        await db.execute(select(LoanApplication).where(LoanApplication.id == app_id).with_for_update())
    ).scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found.")
    return app


def _assert_not_own(app: LoanApplication, officer: User) -> None:
    if app.applicant_id == officer.id:
        raise HTTPException(status_code=403, detail="You cannot review your own application.")


@router.get("/queue")
async def get_queue(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("officer", "admin")),
):
    """Officers: unclaimed applications plus their own in-progress reviews. Admins: everything in review."""
    stmt = select(LoanApplication).where(LoanApplication.status.in_(REVIEWABLE_STATUSES))
    if current_user.role == "officer":
        stmt = stmt.where(officer_visible(current_user))
    result = await db.execute(
        stmt
        # The officer's own in-progress reviews first, then oldest unclaimed work.
        .order_by((LoanApplication.status != "under_review"), LoanApplication.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    return [
        {
            "id": str(a.id),
            "status": a.status,
            "product_type": a.product_type,
            "title": a.title,
            "business_name": a.business_name,
            "loan_amount_requested": float(a.loan_amount_requested) if a.loan_amount_requested is not None else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "reviewed_by": str(a.reviewed_by) if a.reviewed_by else None,
            "assigned_to_me": a.reviewed_by == current_user.id,
            "claim_expired": claim_expired(a),
        }
        for a in result.scalars().all()
    ]


@router.get("/decisions")
async def my_decisions(
    decision: Literal["approved", "rejected"] | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("officer", "admin")),
):
    """Applications the caller approved or rejected, newest decision first, with the recorded reason."""
    statuses = [decision] if decision else ["approved", "rejected"]
    result = await db.execute(
        select(LoanApplication)
        .where(LoanApplication.reviewed_by == current_user.id, LoanApplication.status.in_(statuses))
        .order_by(LoanApplication.reviewed_at.desc().nulls_last())
        .limit(limit)
        .offset(offset)
    )
    return [
        {
            "id": str(a.id),
            "status": a.status,
            "title": a.title,
            "loan_amount_requested": float(a.loan_amount_requested) if a.loan_amount_requested is not None else None,
            "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at else None,
            "review_notes": a.review_notes,
        }
        for a in result.scalars().all()
    ]


@router.post("/{app_id}/claim")
async def claim_application(
    app_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("officer", "admin")),
):
    app = await _load_locked(db, app_id)
    _assert_not_own(app, current_user)
    # The row lock in _load_locked makes a second concurrent claim wait, then see under_review and get 409.
    if app.status not in CLAIMABLE_STATUSES and not claim_expired(app):
        raise HTTPException(status_code=409, detail=f"Cannot start review of an application in status '{app.status}'.")
    app.status = "under_review"
    app.reviewed_by = current_user.id
    app.updated_at = datetime.now(timezone.utc)  # restarts the claim timeout even when re-claiming
    record_db_event(db, event_type="application_claimed", user_id=current_user.id, payload={"application_id": str(app_id)})
    await notify_applicant(db, background_tasks, app)
    await db.commit()
    return {"id": str(app_id), "status": app.status}


@router.post("/{app_id}/decision")
async def make_decision(
    app_id: uuid.UUID,
    body: DecisionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("officer", "admin")),
):
    app = await _load_locked(db, app_id)
    _assert_not_own(app, current_user)
    if app.status not in REVIEWABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"Cannot decide on application with status '{app.status}'.")
    if app.status != "under_review":
        raise HTTPException(status_code=409, detail="Application must be claimed (status: under_review) before a decision can be made.")
    if app.reviewed_by != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=409, detail="Another officer is reviewing this application.")

    app.status = body.decision
    app.reviewed_by = current_user.id
    app.review_notes = body.notes
    app.reviewed_at = datetime.now(timezone.utc)
    record_db_event(
        db,
        event_type="application_decision",
        user_id=current_user.id,
        payload={"application_id": str(app_id), "decision": body.decision, "reason": body.notes},
    )
    await notify_applicant(db, background_tasks, app, [body.notes])
    await db.commit()
    return {"id": str(app_id), "status": app.status}
