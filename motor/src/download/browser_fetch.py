"""Headless-browser rendering for repository pages that need JavaScript.

Some institutional repositories (DSpace 7, Pure, Figshare portals) build the
page — including the link to the file — in the browser, so a plain HTTP
client sees an empty shell. This module renders such a page with Playwright
and returns the PDF, reusing one browser and one session for the whole run.

Deliberate limits:

* **Allowlist only.** Institutional repositories (.edu, .ac.*, and the usual
  repository software paths) and hosts the operator lists in
  ``PAPER_FETCH_BROWSER_HOSTS``. Never shadow-library mirrors.
* **Honest identification.** The browser sends the project's own user agent
  with the operator's contact address; no fingerprint patching, no stealth
  plugins, no attempt to look like someone else. A site that refuses
  automated clients stays refused — that answer is respected, not worked
  around.
* **One request per second**, so a repository is never hit hard.

Disabled unless ``PAPER_FETCH_BROWSER=1``.
"""
from __future__ import annotations

import os
import re
import threading
import time
import urllib.parse
from pathlib import Path

MAX_PDF_SIZE = 50 * 1024 * 1024
MIN_INTERVAL = 1.0

# Repository software leaves these marks in the host or path.
_REPOSITORY_HINTS = (
    "repositorio", "repository", "repositories", "eprints", "dspace", "pure.",
    "digitalcommons", "escholarship", "openaccess", "oa.", "bibliotecadigital",
    "handle.net", "figshare.com", "zenodo.org", "osf.io", "biorxiv.org", "medrxiv.org",
)
_ACADEMIC_TLDS = (".edu", ".edu.br", ".ac.uk", ".ac.jp", ".edu.au")

_lock = threading.Lock()
_next_request_at = 0.0
_playwright = None
_browser = None
_context = None
_unavailable = False


def is_enabled() -> bool:
    return os.environ.get("PAPER_FETCH_BROWSER", "").strip().lower() in {"1", "true", "yes", "on"}


def _extra_hosts() -> set[str]:
    raw = os.environ.get("PAPER_FETCH_BROWSER_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def host_allowed(url: str) -> bool:
    """True for institutional repositories and operator-listed hosts."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    for allowed in _extra_hosts():
        if host == allowed or host.endswith("." + allowed):
            return True
    if any(host.endswith(tld) or f"{tld}." in host for tld in _ACADEMIC_TLDS):
        return True
    if ".ac." in host or ".edu." in host:
        return True
    haystack = host + parsed.path.lower()
    return any(hint in haystack for hint in _REPOSITORY_HINTS)


def user_agent() -> str:
    contact = os.environ.get("UNPAYWALL_EMAIL", "").strip() or os.environ.get("CROSSREF_MAILTO", "").strip()
    suffix = f"; +mailto:{contact}" if contact else ""
    return f"Mozilla/5.0 (compatible; revisao-sistematica/1.0{suffix})"


def _rate_gate() -> None:
    global _next_request_at
    with _lock:
        now = time.monotonic()
        wait = max(0.0, _next_request_at - now)
        _next_request_at = max(now, _next_request_at) + MIN_INTERVAL
    if wait:
        time.sleep(wait)


def _get_context(timeout: int):
    """One browser context for the process, so cookies and TLS are reused."""
    global _playwright, _browser, _context, _unavailable
    if _unavailable:
        return None
    with _lock:
        if _context is not None:
            return _context
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            _unavailable = True
            return None
        try:
            _playwright = sync_playwright().start()
            _browser = _playwright.chromium.launch(headless=True)
            _context = _browser.new_context(
                user_agent=user_agent(),
                accept_downloads=True,
                java_script_enabled=True,
            )
            _context.set_default_timeout(timeout * 1000)
        except Exception:
            _unavailable = True
            _context = None
        return _context


def close() -> None:
    global _playwright, _browser, _context
    with _lock:
        for obj in (_context, _browser, _playwright):
            try:
                (obj.stop if hasattr(obj, "stop") else obj.close)()
            except Exception:
                pass
        _playwright = _browser = _context = None


def fetch_pdf(url: str, *, timeout: int = 30) -> tuple[bytes | None, str | None]:
    """Render ``url`` and return the PDF it serves or links to. (data, error).

    Returns the bytes of the response when the page itself is a PDF, or of
    the first PDF link the rendered DOM exposes.
    """
    if not is_enabled():
        return None, "browser_disabled"
    if not host_allowed(url):
        return None, "host_not_allowlisted"
    context = _get_context(timeout)
    if context is None:
        return None, "playwright_unavailable"

    _rate_gate()
    page = None
    try:
        page = context.new_page()
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        if response is None:
            return None, "no_response"
        body = response.body()
        if body[:5] == b"%PDF-":
            return (body, None) if len(body) <= MAX_PDF_SIZE else (None, "size_exceeded")
        if response.status >= 400:
            return None, f"http_{response.status}"

        html = page.content()
        from pdf_links import extract_pdf_links

        for candidate in extract_pdf_links(html, page.url)[:3]:
            _rate_gate()
            try:
                api_response = context.request.get(candidate, timeout=timeout * 1000)
            except Exception:
                continue
            if api_response.status != 200:
                continue
            data = api_response.body()
            if data[:5] == b"%PDF-" and len(data) <= MAX_PDF_SIZE:
                return data, None
        return None, "no_pdf_on_page"
    except Exception as exc:
        return None, f"browser_error:{re.sub(r'\\s+', ' ', str(exc))[:120]}"
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
