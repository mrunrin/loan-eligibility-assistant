import hashlib
import json
import logging
from datetime import UTC, datetime

from app.config import AUDIT_LOG_PATH

logger = logging.getLogger(__name__)


def write_audit_event(
    *,
    request_id: str,
    session_id: str | None,
    question: str,
    status: str,
    latency_ms: int,
    ttft_ms: int | None = None,
    sources: list[dict] | None = None,
    requested_provider: str | None = None,
    answered_provider: str | None = None,
    intent: str | None = None,
) -> None:
    """Append non-sensitive request metadata for local demo auditability."""
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "request_id": request_id,
        "session_id": session_id,
        "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "status": status,
        "latency_ms": latency_ms,
        "ttft_ms": ttft_ms,
        "requested_provider": requested_provider,
        "answered_provider": answered_provider,
        "intent": intent,
        "source_pages": [source.get("page") for source in sources or []],
    }
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event) + "\n")
    except OSError:
        logger.exception("Audit log write failed")


def record_db_event(db, *, event_type: str, user_id=None, payload: dict | None = None, request_id: str | None = None) -> None:
    """Stage an audit_events row in the caller's transaction (committed with the business change)."""
    from app.models import AuditEvent

    db.add(AuditEvent(event_type=event_type, user_id=user_id, payload=payload or {}, request_id=request_id))
