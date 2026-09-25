"""Institutional access: the operator's own proxy and EZproxy session.

This is the access a university already pays for. Two routes are supported,
both driven entirely by the operator's own configuration:

* an HTTP/SOCKS proxy (``PAPER_FETCH_PROXY``, ``PROXY_URL``, ``HTTPS_PROXY``,
  ``HTTP_PROXY``) — typically the campus proxy or a VPN endpoint;
* an EZproxy server (``EZPROXY_BASE_URL`` plus ``EZPROXY_USER`` /
  ``EZPROXY_PASSWORD``), which is how most libraries hand out licensed
  access off campus. The login form is submitted once and the session
  cookies are reused for the rest of the run.

Nothing here works around a paywall: without the operator's own credentials
or proxy every function below is a no-op.
"""
from __future__ import annotations

import os
import re
import threading
import urllib.parse
from pathlib import Path

try:
    from curl_cffi import requests as _requests
    _IMPERSONATE = {"impersonate": "chrome"}
except ImportError:  # pragma: no cover - exercised only without curl_cffi
    try:
        import requests as _requests
    except ImportError:
        _requests = None
    _IMPERSONATE = {}

MAX_PDF_SIZE = 50 * 1024 * 1024
USER_AGENT = "revisao-sistematica/1.0 (institutional access; +mailto:{})".format(
    os.environ.get("UNPAYWALL_EMAIL", "") or "anonymous"
)

_PROXY_VARS = ("PAPER_FETCH_PROXY", "PROXY_URL", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")

_lock = threading.Lock()
_session = None
_login_done = False
_login_failed = False


def proxy_urls() -> list[str]:
    """Every proxy the operator configured, in the order they were given.

    ``PAPER_FETCH_PROXY`` accepts a comma-separated list, so a campus proxy
    and a VPN endpoint can both be listed and tried in turn. These are the
    operator's own access routes — not a rotating pool for dodging blocks.
    """
    for var in _PROXY_VARS:
        raw = os.environ.get(var, "").strip()
        if raw:
            urls = [u.strip() for u in raw.split(",") if u.strip()]
            if urls:
                return urls
    return []


def proxy_url() -> str | None:
    """The first configured proxy (used where a single value is needed)."""
    urls = proxy_urls()
    return urls[0] if urls else None


def proxies(url: str | None = None) -> dict | None:
    target = url or proxy_url()
    return {"http": target, "https": target} if target else None


def ezproxy_base() -> str | None:
    base = os.environ.get("EZPROXY_BASE_URL", "").strip().rstrip("/")
    return base or None


def is_configured() -> bool:
    """True when at least one institutional route is set up."""
    return bool(proxy_url() or ezproxy_base())


def ezproxy_url(url: str) -> str | None:
    """The EZproxy entry point for a target URL (``/login?url=...``)."""
    base = ezproxy_base()
    if not base or "ezproxy" in urllib.parse.urlparse(url).netloc.lower():
        return None
    return f"{base}/login?url={urllib.parse.quote(url, safe='')}"


def _proxy_label(proxy: str) -> str:
    """Host of a proxy URL, for logs that must never carry its credentials."""
    try:
        return urllib.parse.urlparse(proxy).hostname or "proxy"
    except ValueError:
        return "proxy"


def _cookie_path() -> Path:
    return Path(os.environ.get("PAPER_FETCH_CACHE_DIR", Path.home() / ".cache" / "paper-fetch")) / "ezproxy.cookies"


def _new_session(proxy: str | None = None):
    if _requests is None:
        return None
    session = _requests.Session(**dict(_IMPERSONATE))
    proxy_map = proxies(proxy)
    if proxy_map:
        try:
            session.proxies.update(proxy_map)
        except Exception:
            session.proxies = proxy_map
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _login_fields(html: str) -> dict[str, str]:
    """Hidden inputs of the EZproxy login form, so the POST keeps its state."""
    fields: dict[str, str] = {}
    for match in re.finditer(r"<input\b[^>]*>", html, re.IGNORECASE):
        tag = match.group(0)
        input_type = (re.search(r'type=["\']?([\w-]+)', tag, re.IGNORECASE) or [None, ""])[1].lower()
        name = (re.search(r'name=["\']([^"\']+)', tag, re.IGNORECASE) or [None, ""])[1]
        value = (re.search(r'value=["\']([^"\']*)', tag, re.IGNORECASE) or [None, ""])[1]
        if name and input_type == "hidden":
            fields[name] = value
    return fields


def _ensure_login(session, timeout: int) -> bool:
    """Submit the EZproxy login form once per process. True when usable."""
    global _login_done, _login_failed
    base, user, password = ezproxy_base(), os.environ.get("EZPROXY_USER", ""), os.environ.get("EZPROXY_PASSWORD", "")
    if not base:
        return False
    if _login_done:
        return True
    if _login_failed or not (user and password):
        # Without credentials the server may still authorise by IP/VPN, so the
        # rewritten URL is worth trying; just do not attempt a form login.
        return not (user and password)
    with _lock:
        if _login_done:
            return True
        try:
            page = session.get(f"{base}/login", timeout=timeout)
            fields = _login_fields(page.text or "")
            fields.update({"user": user, "pass": password})
            resp = session.post(f"{base}/login", data=fields, timeout=timeout, allow_redirects=True)
            body = (resp.text or "").lower()
            if resp.status_code >= 400 or "invalid" in body or "incorrect" in body:
                _login_failed = True
                return False
            _login_done = True
            return True
        except Exception:
            _login_failed = True
            return False


def get_session(timeout: int = 30):
    """Shared session carrying the proxy settings and EZproxy cookies."""
    global _session
    with _lock:
        if _session is None:
            _session = _new_session()
    if _session is not None and ezproxy_base():
        _ensure_login(_session, timeout)
    return _session


def fetch_pdf(url: str, *, timeout: int = 30) -> tuple[bytes | None, str | None]:
    """Fetch a URL through the institutional routes. Returns (data, error).

    Tries the proxy first (when set), then the EZproxy-rewritten URL. Only
    PDF bytes are returned; HTML (a login wall, a landing page) is reported
    as an error so the caller keeps looking elsewhere.
    """
    if not is_configured():
        return None, "not_configured"
    session = get_session(timeout)
    if session is None:
        return None, "no_http_client"

    attempts: list[tuple[str, str, str | None]] = []
    for proxy in proxy_urls():
        attempts.append((f"proxy[{_proxy_label(proxy)}]", url, proxy))
    rewritten = ezproxy_url(url)
    if rewritten:
        attempts.append(("ezproxy", rewritten, proxy_url()))

    last_error = "not_attempted"
    for label, target, proxy in attempts:
        attempt_session = session if proxy in (None, proxy_url()) else _new_session(proxy)
        if attempt_session is None:
            continue
        try:
            resp = attempt_session.get(target, timeout=timeout, allow_redirects=True)
        except Exception as exc:
            last_error = f"{label}_error:{exc}"
            continue
        if resp.status_code != 200:
            last_error = f"{label}_http_{resp.status_code}"
            continue
        data = resp.content or b""
        if data[:5] != b"%PDF-":
            last_error = f"{label}_not_a_pdf"
            continue
        if len(data) > MAX_PDF_SIZE:
            last_error = f"{label}_size_exceeded"
            continue
        return data, None
    return None, last_error


def apply_environment() -> str | None:
    """Make ``PAPER_FETCH_PROXY`` visible to every HTTP client in the process.

    urllib (used for the metadata APIs) reads only the standard variables, so
    the project-specific one is mirrored into them when they are unset. The
    operator's own HTTP_PROXY/HTTPS_PROXY always win.
    """
    url = os.environ.get("PAPER_FETCH_PROXY", "").strip() or os.environ.get("PROXY_URL", "").strip()
    if not url:
        return None
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.setdefault(var, url)
    return url
