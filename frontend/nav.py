"""Shared frontend plumbing: backend URL, authenticated API calls, page guards, sidebar."""
import os

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
if API_URL.endswith("/ask/stream"):  # older .env files pointed at the stream endpoint
    API_URL = API_URL[: -len("/ask/stream")]

_HIDE_ALL = (
    "<style>"
    "[data-testid='stSidebarNav']{display:none!important}"
    "[data-testid='collapsedControl']{display:none!important}"
    "[data-testid='stSidebar']{display:none!important}"
    "</style>"
)

_HIDE_NAV = (
    "<style>"
    "[data-testid='stSidebarNav']{display:none!important}"
    "</style>"
)

DASHBOARD_PAGES = {
    "borrower": "pages/3_My_Applications.py",
    "officer": "pages/6_Officer_Queue.py",
    "admin": "pages/7_Admin_Dashboard.py",
}


# ---------------------------------------------------------------------------
# API access
# ---------------------------------------------------------------------------


def auth_headers(extra: dict | None = None) -> dict:
    headers = dict(extra or {})
    token = st.session_state.get("access_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Lets the backend rate-limit per real client instead of per Streamlit container.
    client_ip = getattr(st.context, "ip_address", None)
    if client_ip:
        headers["X-Forwarded-For"] = client_ip
    return headers


def _try_refresh() -> bool:
    refresh_token = st.session_state.get("refresh_token")
    if not refresh_token:
        return False
    try:
        resp = requests.post(
            f"{API_URL}/auth/refresh", json={"refresh_token": refresh_token}, headers=auth_headers(), timeout=10
        )
    except requests.RequestException:
        return False
    if resp.status_code != 200:
        return False
    data = resp.json()
    st.session_state["access_token"] = data["access_token"]
    st.session_state["refresh_token"] = data["refresh_token"]
    return True


def _clear_session() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def api(method: str, path: str, *, timeout: float | tuple = 15, **kwargs) -> requests.Response | None:
    """Call the backend. Refreshes the access token once on 401; logs the user out if that fails.

    Returns None (after showing an error) when the backend cannot be reached.
    """
    headers = kwargs.pop("headers", None)
    for attempt in range(2):
        try:
            resp = requests.request(method, f"{API_URL}{path}", headers=auth_headers(headers), timeout=timeout, **kwargs)
        except requests.exceptions.Timeout:
            st.error("The server took too long to respond. Please try again.")
            return None
        except requests.RequestException:
            st.error("Cannot reach the backend. Make sure the API server is running.")
            return None
        if resp.status_code == 401 and st.session_state.get("access_token") and attempt == 0:
            resp.close()
            if _try_refresh():
                continue
            _clear_session()
            st.warning("Your session expired. Please log in again.")
            st.switch_page("streamlit_app.py")
        return resp
    return None


def detail(resp: requests.Response | None, default: str = "Something went wrong.") -> str:
    """Error message from a backend response, tolerant of non-JSON (e.g. proxy 502) bodies."""
    if resp is None:
        return default
    try:
        body = resp.json()
    except ValueError:
        return default
    value = body.get("detail", default) if isinstance(body, dict) else default
    if isinstance(value, list):  # FastAPI validation errors
        return "\n".join(str(e.get("msg", "")).replace("Value error, ", "") for e in value) or default
    return str(value)


def json_or_none(resp: requests.Response | None, ok: int = 200):
    if resp is None or resp.status_code != ok:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def backend_health() -> dict:
    try:
        return requests.get(f"{API_URL}/health", timeout=3).json()
    except (requests.RequestException, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Page guards
# ---------------------------------------------------------------------------


def require_login() -> dict:
    if "access_token" not in st.session_state:
        st.markdown(
            "<style>[data-testid='stAppViewContainer']{visibility:hidden!important}</style>",
            unsafe_allow_html=True,
        )
        st.switch_page("streamlit_app.py")
        st.stop()
    return st.session_state.get("user", {})


def require_role(*roles: str) -> dict:
    """UX guard only — the backend enforces roles on every endpoint."""
    user = require_login()
    if user.get("role") not in roles:
        st.error("Access denied. You do not have permission to view this page.")
        st.stop()
    return user


_cookie_manager = None  # set by streamlit_app.py on each page load via nav._cookie_manager = _cm


def logout() -> None:
    if st.session_state.get("access_token"):
        try:
            requests.post(f"{API_URL}/auth/logout", headers=auth_headers(), timeout=5)
        except requests.RequestException:
            pass
    _clear_session()
    # streamlit_app.py watches for ?action=logout and clears the browser cookies
    st.query_params["action"] = "logout"
    st.switch_page("streamlit_app.py")


def redirect_to_dashboard() -> None:
    role = st.session_state.get("user", {}).get("role", "borrower")
    st.switch_page(DASHBOARD_PAGES.get(role, DASHBOARD_PAGES["borrower"]))


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _inject_idle_timer(seconds: int = 300) -> None:
    """Redirect to login after N seconds of no mouse, keyboard, scroll, or touch activity."""
    import streamlit.components.v1 as components
    components.html(
        f"""
        <script>
        (function() {{
            var t;
            function reset() {{
                clearTimeout(t);
                t = setTimeout(function() {{
                    window.parent.location.href = '/?idle=1';
                }}, {seconds * 1000});
            }}
            ['mousemove','keydown','click','scroll','touchstart'].forEach(function(e) {{
                window.parent.document.addEventListener(e, reset, true);
            }});
            reset();
        }})();
        </script>
        """,
        height=0,
    )


def hide_sidebar() -> None:
    """Call on every pre-login page — hides sidebar and its toggle completely."""
    st.markdown(_HIDE_ALL, unsafe_allow_html=True)


def render_sidebar(current_page: str) -> None:
    """Call on every post-login page — hides the auto page list, shows user, navigation and logout."""
    st.markdown(_HIDE_NAV, unsafe_allow_html=True)
    _inject_idle_timer(300)
    user = st.session_state.get("user", {})
    role = user.get("role", "borrower")

    with st.sidebar:
        st.markdown(f"**{user.get('full_name') or user.get('email', 'User')}**")
        st.caption(f"Role: {role}")
        st.divider()

        dashboard = DASHBOARD_PAGES.get(role, DASHBOARD_PAGES["borrower"])
        if current_page != dashboard and st.button("← Dashboard", use_container_width=True, key="nav_dashboard"):
            st.switch_page(dashboard)
        if current_page != "pages/8_Chat_Assistant.py" and st.button(
            "AI Loan Assistant", use_container_width=True, key="nav_chat"
        ):
            st.switch_page("pages/8_Chat_Assistant.py")
        if current_page != "pages/9_Security.py" and st.button(
            "Security Settings", use_container_width=True, key="nav_security"
        ):
            st.switch_page("pages/9_Security.py")
        if st.button("Logout", use_container_width=True, key="nav_logout"):
            logout()
