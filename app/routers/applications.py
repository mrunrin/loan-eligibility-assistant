import hashlib
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.audit import record_db_event
from app.config import BASE_DIR, CLAIM_TIMEOUT_HOURS, UPLOAD_DIR
from app.database import get_db
from app.deps import get_current_user
from app.email_utils import send_status_email
from app.models import Document, LoanApplication, User
from app.prequal import MissingFieldsError, apply_prequal, failed_reasons, missing_fields

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/applications", tags=["applications"])

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
_CHUNK = 64 * 1024
# Extension → accepted leading bytes. The extension alone is user-controlled and never trusted.
_MAGIC = {
    ".pdf": (b"%PDF-",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
}
_MEDIA_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
DocumentType = Literal[
    "Photo ID", "Bank Statement", "Salary Slip", "Business Registration Certificate", "GST Certificate", "ITR", "Other"
]
ProductType = Literal["business_loan", "personal_loan", "credit_card"]
# Documents that must be uploaded before an application can be submitted.
REQUIRED_DOCUMENTS = {
    "business_loan": ["Photo ID", "Bank Statement", "Business Registration Certificate"],
    "personal_loan": ["Photo ID", "Salary Slip", "Bank Statement"],
    "credit_card": ["Photo ID", "Bank Statement"],
}
# Once an officer decides, the application is final: no withdrawal or appeal.
DECIDED_STATUSES = {"approved", "rejected", "withdrawn"}
MIN_APPEAL_CHARS = 20
EmploymentType = Literal["salaried", "self_employed"]
# Numeric(15, 2) holds at most 13 integer digits.
_MAX_MONEY = Decimal("9999999999999.99")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ApplicationUpdate(BaseModel):
    # No product caps here: the pre-qual engine owns amount/age rules and must be able to fail them.
    product_type: ProductType | None = None
    loan_amount_requested: Decimal | None = Field(None, gt=0, le=_MAX_MONEY, decimal_places=2)
    loan_purpose: str | None = Field(None, max_length=255)
    credit_score: int | None = Field(None, ge=300, le=900)
    business_name: str | None = Field(None, max_length=255)
    business_type: str | None = Field(None, max_length=100)
    years_in_business: int | None = Field(None, ge=0, le=200)
    annual_revenue: Decimal | None = Field(None, ge=0, le=_MAX_MONEY, decimal_places=2)
    monthly_emi_capacity: Decimal | None = Field(None, ge=0, le=_MAX_MONEY, decimal_places=2)
    employment_type: EmploymentType | None = None
    employer_name: str | None = Field(None, max_length=255)
    date_of_birth: date | None = Field(None, ge=date(1900, 1, 1))
    work_experience_years: int | None = Field(None, ge=0, le=80)
    current_job_years: int | None = Field(None, ge=0, le=80)
    monthly_income: Decimal | None = Field(None, ge=0, le=_MAX_MONEY, decimal_places=2)
    existing_emis: Decimal | None = Field(None, ge=0, le=_MAX_MONEY, decimal_places=2)


class ApplicationCreate(ApplicationUpdate):
    product_type: ProductType = "business_loan"


class ApplicationListOut(BaseModel):
    """Lean list schema — omits financial details to avoid bulk credit score exposure."""
    id: str
    status: str
    product_type: str
    title: str
    loan_amount_requested: float | None
    business_name: str | None
    created_at: str | None = None


class ApplicationOut(BaseModel):
    id: str
    status: str
    product_type: str
    title: str
    loan_amount_requested: float | None
    loan_purpose: str | None
    credit_score: int | None
    business_name: str | None
    business_type: str | None
    years_in_business: int | None
    annual_revenue: float | None
    monthly_emi_capacity: float | None
    employment_type: str | None
    employer_name: str | None
    date_of_birth: date | None
    work_experience_years: int | None
    current_job_years: int | None
    monthly_income: float | None
    existing_emis: float | None
    review_notes: str | None = None
    reviewed_at: str | None = None
    created_at: str | None = None
    appeal_reason: str | None = None
    required_documents: list[str] = []


class AppealRequest(BaseModel):
    reason: str = Field(..., min_length=MIN_APPEAL_CHARS, max_length=2000)


class PreQualOut(BaseModel):
    score: int
    decision: str
    rule_breakdown: list[dict]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _num(value) -> float | None:
    return float(value) if value is not None else None


def _to_list_out(app: LoanApplication) -> ApplicationListOut:
    return ApplicationListOut(
        id=str(app.id),
        status=app.status,
        product_type=app.product_type,
        title=app.title,
        loan_amount_requested=_num(app.loan_amount_requested),
        business_name=app.business_name,
        created_at=app.created_at.isoformat() if app.created_at else None,
    )


def _to_out(app: LoanApplication) -> ApplicationOut:
    return ApplicationOut(
        id=str(app.id),
        status=app.status,
        product_type=app.product_type,
        title=app.title,
        loan_amount_requested=_num(app.loan_amount_requested),
        loan_purpose=app.loan_purpose,
        credit_score=app.credit_score,
        business_name=app.business_name,
        business_type=app.business_type,
        years_in_business=app.years_in_business,
        annual_revenue=_num(app.annual_revenue),
        monthly_emi_capacity=_num(app.monthly_emi_capacity),
        employment_type=app.employment_type,
        employer_name=app.employer_name,
        date_of_birth=app.date_of_birth,
        work_experience_years=app.work_experience_years,
        current_job_years=app.current_job_years,
        monthly_income=_num(app.monthly_income),
        existing_emis=_num(app.existing_emis),
        review_notes=app.review_notes,
        reviewed_at=app.reviewed_at.isoformat() if app.reviewed_at else None,
        created_at=app.created_at.isoformat() if app.created_at else None,
        appeal_reason=app.appeal_reason,
        required_documents=REQUIRED_DOCUMENTS.get(app.product_type, []),
    )


async def notify_applicant(
    db: AsyncSession, background_tasks: BackgroundTasks, app: LoanApplication, details: list[str] | None = None
) -> None:
    """Email the borrower about the application's current status after the response is sent.

    shortcut: FastAPI BackgroundTasks run in the API process and are lost if it restarts mid-send;
    use a queue worker (Celery/RQ/arq) when deploying.
    """
    applicant = await db.get(User, app.applicant_id)
    if applicant:
        background_tasks.add_task(
            send_status_email, applicant.email, applicant.full_name or "", app.title, app.status, details or []
        )


async def _load_app(db: AsyncSession, app_id: uuid.UUID, *, lock: bool = False) -> LoanApplication:
    stmt = select(LoanApplication).where(LoanApplication.id == app_id)
    if lock:
        stmt = stmt.with_for_update()
    app = (await db.execute(stmt)).scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found.")
    return app


def _assert_owner(app: LoanApplication, user: User) -> None:
    if app.applicant_id != user.id:
        raise HTTPException(status_code=403, detail="Not your application.")


def assert_can_view(app: LoanApplication, user: User) -> None:
    """Borrowers see their own; officers see unassigned work and their own assignments; admins see all."""
    if user.role == "borrower":
        _assert_owner(app, user)
    elif user.role == "officer" and app.reviewed_by not in (None, user.id) and not claim_expired(app):
        raise HTTPException(status_code=403, detail="This application is assigned to another officer.")


def _claim_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=CLAIM_TIMEOUT_HOURS)


def claim_expired(app: LoanApplication) -> bool:
    """An under_review claim untouched for CLAIM_TIMEOUT_HOURS is abandoned and open to any officer."""
    return app.status == "under_review" and app.updated_at is not None and app.updated_at < _claim_cutoff()


def officer_visible(user: User):
    """SQL counterpart of assert_can_view for officer list queries."""
    return or_(
        LoanApplication.reviewed_by.is_(None),
        LoanApplication.reviewed_by == user.id,
        and_(LoanApplication.status == "under_review", LoanApplication.updated_at < _claim_cutoff()),
    )




def _resolve_stored_path(file_path: str) -> Path | None:
    """Map a stored path to disk, refusing anything outside the upload roots."""
    path = Path(file_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    path = path.resolve()
    roots = {UPLOAD_DIR.resolve(), (BASE_DIR / "uploads").resolve()}
    if not any(path.is_relative_to(root) for root in roots):
        return None
    return path


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
async def create_application(
    body: ApplicationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = LoanApplication(applicant_id=current_user.id, **body.model_dump())
    db.add(app)
    await db.commit()
    await db.refresh(app)
    return _to_out(app)


@router.get("", response_model=list[ApplicationListOut])
async def list_applications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(LoanApplication).order_by(LoanApplication.created_at.desc()).limit(limit).offset(offset)
    if current_user.role == "borrower":
        stmt = stmt.where(LoanApplication.applicant_id == current_user.id)
    elif current_user.role == "officer":
        # Officers only work submitted applications; drafts belong to the borrower alone
        stmt = stmt.where(LoanApplication.status != "draft", officer_visible(current_user))
    result = await db.execute(stmt)
    return [_to_list_out(a) for a in result.scalars().all()]


@router.get("/{app_id}", response_model=ApplicationOut)
async def get_application(
    app_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = await _load_app(db, app_id)
    assert_can_view(app, current_user)
    return _to_out(app)


@router.patch("/{app_id}", response_model=ApplicationOut)
async def update_application(
    app_id: uuid.UUID,
    body: ApplicationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = await _load_app(db, app_id, lock=True)
    _assert_owner(app, current_user)
    if app.status != "draft":
        raise HTTPException(status_code=400, detail="Only draft applications can be edited.")
    # exclude_unset (not exclude_none) so a client can clear a field by sending null
    changes = body.model_dump(exclude_unset=True)
    if "product_type" in changes and changes["product_type"] is None:
        raise HTTPException(status_code=422, detail="product_type cannot be cleared.")
    for field, value in changes.items():
        setattr(app, field, value)
    await db.commit()
    await db.refresh(app)
    return _to_out(app)


@router.post("/{app_id}/submit", response_model=PreQualOut)
async def submit_application(
    app_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # The row lock serialises double-clicks: the second request waits, sees a non-draft status and gets 409.
    app = await _load_app(db, app_id, lock=True)
    _assert_owner(app, current_user)
    if app.status != "draft":
        raise HTTPException(status_code=409, detail="Application already submitted.")
    if missing := missing_fields(app):
        raise HTTPException(status_code=422, detail=str(MissingFieldsError(missing)))
    uploaded = set((await db.execute(
        select(Document.document_type).where(Document.application_id == app_id)
    )).scalars())
    missing_docs = [d for d in REQUIRED_DOCUMENTS[app.product_type] if d not in uploaded]
    if missing_docs:
        raise HTTPException(status_code=422, detail=f"Upload these documents before submitting: {', '.join(missing_docs)}.")
    try:
        prequal = await apply_prequal(db, app)
        await notify_applicant(db, background_tasks, app, failed_reasons(prequal) if prequal.decision == "fail" else None)
        await db.commit()
    except MissingFieldsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Application was submitted concurrently.") from exc
    return PreQualOut(score=prequal.score, decision=prequal.decision, rule_breakdown=prequal.rule_breakdown)


@router.post("/{app_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    app_id: uuid.UUID,
    document_type: DocumentType = "Other",
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = await _load_app(db, app_id, lock=True)
    _assert_owner(app, current_user)
    if app.status != "draft":
        raise HTTPException(status_code=409, detail="Documents can only be added to draft applications.")

    safe_name = Path(file.filename or "").name  # strips any directory components (path traversal)
    suffix = Path(safe_name).suffix.lower()
    if not safe_name or suffix not in _MAGIC:
        raise HTTPException(status_code=415, detail=f"File type {suffix or '(none)'} not allowed.")
    stem = Path(safe_name).stem[: 200 - len(suffix)]
    safe_name = f"{stem}{suffix}"

    dest_dir = UPLOAD_DIR / str(app_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{uuid.uuid4().hex}_{safe_name}"

    size = 0
    digest = hashlib.sha256()
    try:
        first = await file.read(_CHUNK)
        if not first.startswith(_MAGIC[suffix]):
            raise HTTPException(status_code=415, detail="File content does not match its extension.")
        async with aiofiles.open(dest_path, "wb") as out:
            chunk = first
            while chunk:
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise HTTPException(status_code=413, detail="File exceeds 10 MB limit.")
                digest.update(chunk)
                await out.write(chunk)
                chunk = await file.read(_CHUNK)

        doc = Document(
            application_id=app.id,
            document_type=document_type,
            file_name=safe_name,
            file_path=str(dest_path),
            sha256=digest.hexdigest(),
        )
        db.add(doc)
        await db.flush()  # assigns doc.id for the audit row
        record_db_event(db, event_type="document_uploaded", user_id=current_user.id, payload={
            "application_id": str(app_id), "document_id": str(doc.id), "document_type": document_type,
            "sha256": doc.sha256, "bytes": size,
        })
        await db.commit()
    except BaseException:
        dest_path.unlink(missing_ok=True)  # never leave an orphan file when the request fails
        raise
    return {"id": str(doc.id), "file_name": safe_name, "document_type": document_type}


@router.get("/{app_id}/documents")
async def list_documents(
    app_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = await _load_app(db, app_id)
    assert_can_view(app, current_user)
    result = await db.execute(
        select(Document).where(Document.application_id == app_id).order_by(Document.uploaded_at)
    )
    return [
        {
            "id": str(d.id),
            "file_name": d.file_name,
            "document_type": d.document_type,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            "sha256": d.sha256,
        }
        for d in result.scalars().all()
    ]


@router.get("/{app_id}/documents/{doc_id}/download")
async def download_document(
    app_id: uuid.UUID,
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = await _load_app(db, app_id)
    assert_can_view(app, current_user)
    doc = (
        await db.execute(select(Document).where(Document.id == doc_id, Document.application_id == app_id))
    ).scalar_one_or_none()
    if not doc or not doc.file_path:
        raise HTTPException(status_code=404, detail="Document not found.")
    path = _resolve_stored_path(doc.file_path)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Document file is missing.")
    if doc.sha256:  # files uploaded before hashing was added have none
        async with aiofiles.open(path, "rb") as f:
            stored = hashlib.sha256(await f.read()).hexdigest()  # files are capped at 10 MB
        if stored != doc.sha256:
            logger.error("Document %s failed its integrity check", doc.id)
            record_db_event(db, event_type="document_integrity_failed", user_id=current_user.id, payload={
                "application_id": str(app_id), "document_id": str(doc_id), "expected": doc.sha256, "found": stored,
            })
            await db.commit()  # keep the event although the request fails
            raise HTTPException(status_code=409, detail="This document failed its integrity check. Contact support.")
    # Who opened which borrower document: officers and admins can read files they do not own.
    record_db_event(db, event_type="document_downloaded", user_id=current_user.id, payload={
        "application_id": str(app_id), "document_id": str(doc_id), "viewer_role": current_user.role,
    })
    await db.commit()
    return FileResponse(
        path,
        filename=doc.file_name or path.name,
        media_type=_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
    )


@router.post("/{app_id}/withdraw")
async def withdraw_application(
    app_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A status change, not a deletion: the record is kept. Allowed only before an officer decides."""
    app = await _load_app(db, app_id, lock=True)
    _assert_owner(app, current_user)
    if app.status in DECIDED_STATUSES:
        raise HTTPException(status_code=409, detail=f"Cannot withdraw an application that is already {app.status}.")
    previous = app.status
    app.status = "withdrawn"
    record_db_event(db, event_type="application_withdrawn", user_id=current_user.id,
                    payload={"application_id": str(app_id), "from": previous})
    await notify_applicant(db, background_tasks, app)
    await db.commit()
    return {"id": str(app_id), "status": app.status}


@router.post("/{app_id}/appeal")
async def appeal_application(
    app_id: uuid.UUID,
    body: AppealRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Borrower contests a pre-qualification fail; a loan officer then reviews it manually."""
    app = await _load_app(db, app_id, lock=True)
    _assert_owner(app, current_user)
    if app.status != "pre_qual_fail":
        raise HTTPException(status_code=409, detail="Only applications that failed pre-qualification can be appealed.")
    app.status = "manual_review"
    app.appeal_reason = body.reason.strip()
    record_db_event(db, event_type="application_appealed", user_id=current_user.id,
                    payload={"application_id": str(app_id), "reason": app.appeal_reason})
    await notify_applicant(db, background_tasks, app)
    await db.commit()
    return {"id": str(app_id), "status": app.status}
