import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.audit import record_db_event
from app.database import get_db
from app.deps import require_role
from app.models import LoanApplication, User

router = APIRouter(prefix="/admin", tags=["admin"])


class RoleUpdate(BaseModel):
    role: Literal["borrower", "officer", "admin"]


async def _load_other_user(db: AsyncSession, user_id: uuid.UUID, current_user: User, action: str) -> User:
    # Compared as UUIDs, so case or formatting tricks in the path cannot bypass the self-check.
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail=f"Admins cannot {action}.")
    user = (await db.execute(select(User).where(User.id == user_id).with_for_update())).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


@router.get("/users")
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    result = await db.execute(select(User).order_by(User.created_at).offset(skip).limit(limit))
    return [
        {"id": str(u.id), "email": u.email, "full_name": u.full_name, "role": u.role, "is_active": u.is_active}
        for u in result.scalars().all()
    ]


@router.post("/users/{user_id}/role")
async def update_role(
    user_id: uuid.UUID,
    body: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    user = await _load_other_user(db, user_id, current_user, "change their own role")
    # No super-admin: one admin cannot demote another (demoting first would also bypass the deactivation guard).
    if user.role == "admin":
        raise HTTPException(status_code=403, detail="Admins cannot change another admin's role.")
    old_role = user.role
    user.role = body.role
    record_db_event(
        db,
        event_type="role_changed",
        user_id=current_user.id,
        payload={"target_user_id": str(user_id), "from": old_role, "to": body.role},
    )
    await db.commit()
    return {"id": str(user_id), "role": user.role}


@router.post("/users/{user_id}/active")
async def toggle_active(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    user = await _load_other_user(db, user_id, current_user, "deactivate themselves")
    # One admin cannot lock another out; re-activating an inactive admin is still allowed.
    if user.role == "admin" and user.is_active:
        raise HTTPException(status_code=403, detail="Admins cannot deactivate other admins.")
    user.is_active = not user.is_active
    if not user.is_active:
        user.refresh_token_hash = None  # end the deactivated user's session immediately
    record_db_event(
        db,
        event_type="user_active_toggled",
        user_id=current_user.id,
        payload={"target_user_id": str(user_id), "is_active": user.is_active},
    )
    await db.commit()
    return {"id": str(user_id), "is_active": user.is_active}


@router.get("/stats")
async def stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    status_counts = await db.execute(
        select(LoanApplication.status, func.count().label("count")).group_by(LoanApplication.status)
    )
    role_counts = await db.execute(select(User.role, func.count().label("count")).group_by(User.role))
    return {
        "applications_by_status": {row.status: row.count for row in status_counts},
        "users_by_role": {row.role: row.count for row in role_counts},
    }
