import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.audit import record_db_event
from app.case_summary import case_summary
from app.database import get_db
from app.deps import get_current_user, require_role
from app.models import LoanApplication, PreQualificationResult, User
from app.prequal import MissingFieldsError, apply_prequal, failed_reasons
from app.routers.applications import assert_can_view, notify_applicant

router = APIRouter(prefix="/prequal", tags=["prequal"])

# Re-running is a pre-decision action: drafts were never submitted and finalised apps must stay final.
RERUNNABLE_STATUSES = {"submitted", "pre_qual_pass", "pre_qual_fail", "manual_review"}


@router.post("/{app_id}/run")
async def run_prequal_endpoint(
    app_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("officer", "admin")),
):
    app = (
        await db.execute(select(LoanApplication).where(LoanApplication.id == app_id).with_for_update())
    ).scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found.")
    if app.status not in RERUNNABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"Pre-qualification cannot be re-run in status '{app.status}'.")
    if app.applicant_id == current_user.id:
        raise HTTPException(status_code=403, detail="You cannot re-run pre-qualification on your own application.")
    try:
        prequal = await apply_prequal(db, app)
        record_db_event(
            db,
            event_type="prequal_rerun",
            user_id=current_user.id,
            payload={"application_id": str(app_id), "decision": prequal.decision, "score": prequal.score},
        )
        await notify_applicant(db, background_tasks, app, failed_reasons(prequal) if prequal.decision == "fail" else None)
        await db.commit()
    except MissingFieldsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Pre-qualification was updated concurrently; retry.") from exc
    return {"score": prequal.score, "decision": prequal.decision, "rule_breakdown": prequal.rule_breakdown}


@router.get("/{app_id}/summary")
async def get_case_summary(
    app_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("officer")),
):
    """5–6 line AI summary of the pre-qualification result for the reviewing officer (rules text as fallback).

    Officers only: admins keep the plain rule breakdown.
    """
    app = (await db.execute(select(LoanApplication).where(LoanApplication.id == app_id))).scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found.")
    assert_can_view(app, current_user)
    record = (
        await db.execute(select(PreQualificationResult).where(PreQualificationResult.application_id == app_id))
    ).scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="No pre-qualification result found.")
    return await case_summary(app, record)


@router.get("/{app_id}")
async def get_prequal_result(
    app_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = (await db.execute(select(LoanApplication).where(LoanApplication.id == app_id))).scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found.")
    assert_can_view(app, current_user)
    record = (
        await db.execute(select(PreQualificationResult).where(PreQualificationResult.application_id == app_id))
    ).scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="No pre-qualification result found.")
    return {
        "score": record.score,
        "decision": record.decision,
        "rule_breakdown": record.rule_breakdown,
        "ran_at": record.ran_at.isoformat() if record.ran_at else None,
    }
