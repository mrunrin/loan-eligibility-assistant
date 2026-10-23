"""One shared rate limiter for the whole app.

All browser traffic reaches the backend through the Streamlit container, so keying on the
TCP peer would put every user in one bucket. The frontend forwards the real client IP in
X-Forwarded-For; it is honoured only when the peer is a configured trusted proxy.
"""

import ipaddress

from fastapi import Request
from jose import JWTError
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.auth import decode_token
from app.config import TRUSTED_PROXIES

_TRUSTED_NETS = [ipaddress.ip_network(p, strict=False) for p in TRUSTED_PROXIES]


def _is_trusted(peer: str) -> bool:
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in _TRUSTED_NETS)


def client_ip(request: Request) -> str:
    peer = get_remote_address(request)
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and _is_trusted(peer):
        return forwarded.split(",")[0].strip() or peer
    return peer


def user_or_ip(request: Request) -> str:
    """Per-user bucket for authenticated endpoints; falls back to client IP."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            payload = decode_token(auth[7:])
            if payload.get("type") == "access" and payload.get("sub"):
                return f"user:{payload['sub']}"
        except JWTError:
            pass
    return f"ip:{client_ip(request)}"


limiter = Limiter(key_func=client_ip)
