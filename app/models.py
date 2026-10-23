import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# ---------------------------------------------------------------------------
# Enum types — PostgreSQL native ENUMs (not TEXT)
# ---------------------------------------------------------------------------

UserRoleEnum = Enum("borrower", "officer", "admin", name="user_role")

ApplicationStatusEnum = Enum(
    "draft",
    "submitted",
    "pre_qual_pass",
    "pre_qual_fail",
    "manual_review",
    "under_review",
    "approved",
    "rejected",
    "withdrawn",
    name="application_status",
)

PrequalDecisionEnum = Enum("pass", "fail", "manual_review", name="prequal_decision")

ProductTypeEnum = Enum("business_loan", "personal_loan", "credit_card", name="product_type")
PRODUCT_LABELS = {"business_loan": "Business Loan", "personal_loan": "Personal Loan", "credit_card": "Credit Card"}


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(UserRoleEnum, nullable=False, default="borrower")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="true")
    refresh_token_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    # TOTP secrets are Fernet-encrypted (app.auth.encrypt_secret); never stored in plain text
    totp_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_pending_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_last_counter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mfa_failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    # Bumped on logout and password change — any JWT with an older version is rejected instantly
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Lockouts since the last successful sign-in; each one doubles the next lockout length.
    lockout_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    # Server-side session limits (app.auth.session_expired): absolute length and idle time.
    session_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Email verification — token hash stored, never the plain token
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verify_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email_verify_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_verify_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    applications: Mapped[list["LoanApplication"]] = relationship(
        "LoanApplication",
        back_populates="applicant",
        foreign_keys="LoanApplication.applicant_id",
    )
    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        "ChatSession", back_populates="user"
    )


class LoanApplication(Base):
    __tablename__ = "loan_applications"
    # The officer queue filters by status and sorts by created_at (migration 012).
    __table_args__ = (Index("ix_loan_applications_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        ApplicationStatusEnum, nullable=False, default="draft", index=True
    )
    # One table for all products: shared columns plus per-product nullable ones.
    # Which columns are required is decided per product in app.prequal.REQUIRED_FIELDS.
    product_type: Mapped[str] = mapped_column(
        ProductTypeEnum, nullable=False, default="business_loan", server_default="business_loan", index=True
    )
    loan_amount_requested: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    loan_purpose: Mapped[str | None] = mapped_column(String(255))
    credit_score: Mapped[int | None] = mapped_column(Integer)
    # Business loan
    business_name: Mapped[str | None] = mapped_column(String(255))
    business_type: Mapped[str | None] = mapped_column(String(100))
    years_in_business: Mapped[int | None] = mapped_column(Integer)
    annual_revenue: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    monthly_emi_capacity: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    # Personal loan / credit card (individual applicant)
    employment_type: Mapped[str | None] = mapped_column(String(20))
    employer_name: Mapped[str | None] = mapped_column(String(255))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    work_experience_years: Mapped[int | None] = mapped_column(Integer)
    current_job_years: Mapped[int | None] = mapped_column(Integer)
    monthly_income: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    existing_emis: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    review_notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Borrower's written appeal against a pre_qual_fail; the appeal moves the application to manual_review.
    appeal_reason: Mapped[str | None] = mapped_column(Text)

    applicant: Mapped["User"] = relationship(
        "User", back_populates="applications", foreign_keys=[applicant_id], passive_deletes=True
    )
    reviewer: Mapped["User | None"] = relationship(
        "User", foreign_keys=[reviewed_by]
    )
    prequal_result: Mapped["PreQualificationResult | None"] = relationship(
        "PreQualificationResult", back_populates="application", uselist=False
    )
    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="application"
    )

    @property
    def title(self) -> str:
        """Display name used by every list, queue and chat view."""
        label = PRODUCT_LABELS.get(self.product_type, "Application")
        name = (self.business_name if self.product_type == "business_loan" else self.employer_name) or ""
        return f"{label} · {name.strip()}" if name.strip() else label


class PreQualificationResult(Base):
    __tablename__ = "pre_qualification_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("loan_applications.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(PrequalDecisionEnum, nullable=False)
    # Stores per-rule breakdown: [{"rule": "...", "passed": true, "score": 20, "message": "..."}]
    rule_breakdown: Mapped[dict | None] = mapped_column(JSONB)
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    application: Mapped["LoanApplication"] = relationship(
        "LoanApplication", back_populates="prequal_result"
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("loan_applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[str | None] = mapped_column(String(100))
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_path: Mapped[str | None] = mapped_column(String(500))
    # SHA-256 of the stored bytes, checked on every download (tamper evidence).
    sha256: Mapped[str | None] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    application: Mapped["LoanApplication"] = relationship(
        "LoanApplication", back_populates="documents"
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    external_session_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User | None"] = relationship("User", back_populates="chat_sessions")


class ChatMessage(Base):
    """Persisted conversation turns — short-term memory that survives restarts and multiple workers."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # CASCADE: deleting an account erases its chat history
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_chat_messages_user_session_created", "user_id", "session_id", "created_at"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[str | None] = mapped_column(String(100), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str | None] = mapped_column(String(50), index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
