import os
import tempfile

# Settings are read at import time, so test defaults must be set before any app module loads.
os.environ.setdefault("JWT_SECRET_KEY", "test-only-jwt-secret-0123456789abcdef0123456789")
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="loan-uploads-")
os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")

import pytest  # noqa: E402


@pytest.fixture
def anyio_backend():
    return "asyncio"
