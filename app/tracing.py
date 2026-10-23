from typing import Any

from dotenv import load_dotenv
from langfuse import Langfuse, get_client

load_dotenv()


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
