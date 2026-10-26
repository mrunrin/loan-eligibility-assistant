from typing import Any

from dotenv import load_dotenv
from langfuse import Langfuse, get_client, propagate_attributes

load_dotenv()

# Promptfoo sends "eval-..." session ids (promptfooconfig.yaml); eval scripts set LANGFUSE_TRACING_ENVIRONMENT.
EVAL_SESSION_PREFIX = "eval-"


def _mask(data: Any = None, **_: Any) -> Any:
    """Redact PII from every traced input/output before it leaves the process."""
    from app.guardrails_config import redact_pii

    if isinstance(data, str):
        return redact_pii(data)
    if isinstance(data, dict):
        return {k: _mask(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_mask(v) for v in data]
    return data


# Constructing the client first registers it as the singleton that get_client() returns.
Langfuse(mask=_mask)
langfuse = get_client()


def trace_environment(session_id: str | None):
    """Put evaluation requests in the Langfuse "eval" environment so they don't skew real-traffic latency."""
    is_eval = (session_id or "").startswith(EVAL_SESSION_PREFIX)
    return propagate_attributes(environment="eval" if is_eval else None)
