#!/usr/bin/env python3
"""Fetch legal open-access PDFs by DOI.

Resolution order: Unpaywall -> Semantic Scholar openAccessPdf ->
arXiv -> PMC OA -> bioRxiv/medRxiv.

Exit codes:
  0  success (all DOIs resolved and downloaded / dry-run previewed)
  1  unresolved — one or more DOIs had no OA copy; no transport failure
  2  reserved for auth errors (currently unused; Unpaywall gracefully degrades)
  3  validation error (bad arguments, missing input)
  4  transport error — network / download / IO failure (retryable class)

If UNPAYWALL_EMAIL is not set, the Unpaywall source is skipped
and the remaining 4 sources are still tried.

Machine contract:
  stdout — one JSON object per invocation (or NDJSON with --stream)
  stderr — NDJSON progress events when --format json; prose when --format text

Contract-changing version of this file. The schema_version below is what the
`schema` subcommand reports and what appears in every response's `meta` slot;
agents that cache schema should compare against it to detect drift.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html.parser
import ipaddress
import json
import functools
import os
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import typing
import time
import threading
from pdf_links import extract_pdf_links
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from bypass403 import bypass_download_pdf, bypass_get, default_engine as bypass_engine, validate_pdf_data
import identity as _identity
import http_retry
import institutional
import fulltext_xml
import browser_fetch
import pmc_s3
# ---------------------------------------------------------------------------
# Sci-Hub circuit breaker and timeout
# ---------------------------------------------------------------------------
SCIHUB_TIMEOUT = 10   # seconds for Sci-Hub attempts

# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

CLI_VERSION = "0.16.0"
SCHEMA_VERSION = "1.12.0"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

EMAIL = os.environ.get("UNPAYWALL_EMAIL", "").strip()
CORE_API_KEY = os.environ.get("CORE_API_KEY", "").strip()
# UA for API calls (Unpaywall requires contact email in the UA per their ToS).
UA = f"paper-fetch/{CLI_VERSION} (mailto:{EMAIL or 'anonymous'})"
# UA for PDF downloads — some publishers (e.g., iiarjournals.org) return
# HTTP 403 for non-browser User-Agents even on OA PDFs. Uses a generic
# modern browser identifier; the per-request Accept header still declares
# we want a PDF, and the host allowlist still restricts where we fetch.
DOWNLOAD_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
DEFAULT_TIMEOUT = 30
MAX_PDF_SIZE = 50 * 1024 * 1024  # 50 MB

# Canonical DOI shape — kept here so build_schema() and runtime validation
# share one source of truth. Schema-side this is exposed as the regex below
# without the surrounding anchors.


def normalize_doi(doi: str) -> str:
    """Normalize a DOI string to the canonical form: 10.xxxx/xxxx.

    Delegates to ``identity.normalize_doi`` — the single source of truth for
    DOI normalization shared with the cache/index layer and the identity
    validator, so a DOI is never compared against itself under two subtly
    different normalization rules.
    """
    return _identity.normalize_doi(doi)


def record_dois(record: dict) -> list[str]:
    """Extract DOI(s) from a CORE API record.

    Looks at:
      - record["doi"]
      - record["identifiers"] (if present and is a list) for entries that have a "doi" key or are DOIs themselves.
    Returns a list of DOI strings (may be empty).
    """
    dois = []
    # Direct doi field
    if record.get("doi"):
        dois.append(str(record["doi"]))
    # Identifiers field
    identifiers = record.get("identifiers")
    if isinstance(identifiers, list):
        for ident in identifiers:
            if isinstance(ident, dict) and ident.get("doi"):
                dois.append(str(ident["doi"]))
            elif isinstance(ident, str):
                # If the identifier is a string, check if it looks like a DOI
                ident_stripped = ident.strip()
                if ident_stripped.lower().startswith("doi:"):
                    identifico = ident_stripped[4:].strip()
                    if identifico:
                        dois.append(identifico)
                elif ident_stripped.lower().startswith("http://doi.org/") or ident_stripped.lower().startswith("https://doi.org/"):
                    # Extract the DOI part
                    if ident_stripped.lower().startswith("http://doi.org/"):
                        identifico = ident_stripped[17:].strip()
                    else:
                        identifico = ident_stripped[19:].strip()
                    if identifico:
                        dois.append(identifico)
    return dois


# Per-error retry backoff hints surfaced to agents. Only set on retryable=True
# codes. Values are recommendations, not guarantees: an orchestrator that
# ignores them and retries sooner will at worst re-hit the same failure.
RETRY_AFTER_HOURS = {
    "not_found": 168,              # OA availability changes on embargo / preprint timescale
    "resolve_network_error": 1,    # resolver APIs unreachable; availability unknown
    "download_network_error": 1,   # transient network / upstream hiccup
    "download_size_exceeded": 24,  # publisher posted a >50 MB PDF; revisit in a day
    "download_io_error": 1,        # local disk full / permission blip
}

# ---------------------------------------------------------------------------
# Institutional mode
# ---------------------------------------------------------------------------

# Rate limit (institutional mode only — public OA sources are unmetered by
# their operators and do not need client-side pacing).
INSTITUTIONAL_RATE_PER_SEC = 1.0

# ---------------------------------------------------------------------------
# Sci-Hub fallback
# ---------------------------------------------------------------------------

## Default mirror list (snapshot from https://www.sci-hub.pub/ on 2026-04-26).
# Operator can override with PAPER_FETCH_SCIHUB_MIRRORS=sci-hub.ru,sci-hub.st,...
SCIHUB_DEFAULT_MIRRORS = (
    "sci-hub.ru",
    "sci-hub.al",
    "sci-hub.mk",
    "sci-hub.su",
    "sci-hub.ee",
    "sci.bban.top",
    "sci-net.xyz",
)
SCIHUB_DISCOVERY_URL = "https://www.sci-hub.pub/"

# Mobile Safari UA for Sci-Hub HTML page fetches. Mobile clients tend to
# get a simpler page layout less likely to trigger CAPTCHA. Technique
# borrowed from ethanwillis/zotero-scihub.
SCIHUB_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 11_3_1 like Mac OS X) AppleWebKit/603.1.30 (KHTML, like Gecko) Version/10.0 Mobile/14E304 Safari/602.1"

# Polite per-host pacing for Sci-Hub mirror requests. Public OA APIs are
# unmetered; Sci-Hub mirrors throttle and CAPTCHA aggressively, so we pace
# Sci-Hub fetches independently of institutional mode.
SCIHUB_RATE_PER_SEC = 1.0
_last_scihub_request_monotonic: float = 0.0

# ---------------------------------------------------------------------------
# Anna's Archive fallback
# ---------------------------------------------------------------------------

ANNAS_ARCHIVE_DEFAULT_MIRRORS = (
    "https://annas-archive.gl",
    "https://annas-archive.pk",
    "https://annas-archive.gd",
    "https://annas-archive.li",
)

# ---------------------------------------------------------------------------
# Libgen fallback
# ---------------------------------------------------------------------------

LIBGEN_DEFAULT_MIRRORS = (
    "https://libgen.li",
)
LIBGEN_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
LIBGEN_RATE_PER_SEC = 2.0
_last_libgen_request_monotonic: float = 0.0

# Context with relaxed hostname/cert checks for fallback mirror exploration
_ssl_unverified_context = ssl.create_default_context()
_ssl_unverified_context.check_hostname = False
_ssl_unverified_context.verify_mode = ssl.CERT_NONE


# Hostnames blocked in every mode. Covers two threat classes:
#   - loopback aliases that resolve to 127.0.0.1 / ::1 but pass the IP literal
#     check (the ip literal check only fires when the URL host IS an IP)
#   - cloud metadata endpoints that can leak IAM credentials if an SSRF
#     target pivoted into fetching from them
# A hostname that resolves into private space (whether by intent or DNS
# rebinding) is caught separately by _host_addrs_safe(), which getaddrinfo()s
# every fetch target and rejects private/loopback/link-local/reserved/
# unspecified answers. This set only short-circuits the well-known aliases.
#
# KEEP IN SYNC with the identical _BLOCKED_HOSTS set in cloak_pdf.py — the cloak
# companion runs as its own process and re-implements the SSRF gate rather than
# importing it, so a host added here must be added there too.
_BLOCKED_HOSTS = {
    # Loopback aliases
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    # Cloud metadata
    "metadata.google.internal",
    "metadata.aws.internal",
    "metadata",  # some cloud SDKs resolve bare 'metadata'
}


def _is_institutional() -> bool:
    """True iff the operator has opted the process into institutional mode."""
    val = os.environ.get("PAPER_FETCH_INSTITUTIONAL", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _auth_mode() -> str:
    return "institutional" if _is_institutional() else "public"


# ---------------------------------------------------------------------------
# CloakBrowser fallback (operator-controlled, off by default)
# ---------------------------------------------------------------------------

# When PAPER_FETCH_CLOAK is set, a download blocked by Cloudflare (HTTP 403/429
# or an HTML interstitial served in place of the PDF) is retried through
# CloakBrowser — a stealth Chromium that can pass the JS challenge. fetch.py
# shells out to the companion `cloak_pdf.py` via a cloakbrowser-importable
# Python, so this file keeps its stdlib-only footprint. Bytes returned by the
# helper are re-validated through the same %PDF + size checks as any other
# download; the agent cannot opt in (env var is an operator action).

# URLs that were ultimately downloaded via CloakBrowser, so the result envelope
# can flag `via: cloak` for orchestrator visibility.
_CLOAK_DOWNLOADS: set[str] = set()


def _is_cloak_enabled() -> bool:
    """True iff the operator opted into the CloakBrowser fallback."""
    return bool(os.environ.get("PAPER_FETCH_CLOAK"))


@functools.lru_cache(maxsize=1)
def _resolve_cloak_python() -> str | None:
    """Find a Python interpreter that can import CloakBrowser or Playwright."""
    candidates = [
        os.environ.get("CLOAKBROWSER_PYTHON", "").strip(),
        sys.executable,
        str(Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"),
    ]
    seen: set[str] = set()

    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        if not (os.path.isfile(candidate) or shutil.which(candidate)):
            continue

        for check_mod in ("cloakbrowser", "playwright"):
            try:
                result = subprocess.run(
                    [candidate, "-c", f"import {check_mod}"],
                    capture_output=True,
                    timeout=20,
                )
                if result.returncode == 0:
                    return candidate
            except Exception:
                continue

    return None


def _cloak_fetch_pdf(url: str, *, timeout: int) -> bytes | None:
    """Fetch PDF bytes through CloakBrowser / Playwright fallback. Returns bytes, or None on failure."""
    left = _time_left()
    if left is not None and left < 15:
        return None  # not enough budget left to start a browser
    py = _resolve_cloak_python()
    if not py:
        _progress("download_cloak_skip", url=url, reason="no_cloakbrowser_python")
        return None
    helper = Path(__file__).resolve().with_name("cloak_pdf.py")
    if not helper.exists():
        _progress("download_cloak_skip", url=url, reason="helper_missing")
        return None
    try:
        # Generous timeout for headless browser startup and challenge resolution
        cloak_timeout = max(15, min(_bounded_timeout(timeout), 35))
        r = subprocess.run(
            [py, str(helper), url, str(cloak_timeout)],
            capture_output=True,
            timeout=cloak_timeout + 5,
        )
    except Exception as e:
        _progress("download_cloak_error", url=url, error=str(e))
        return None
    if r.returncode != 0 or not r.stdout:
        tail = r.stderr.decode("utf-8", "replace")[-200:] if r.stderr else ""
        _progress("download_cloak_error", url=url, reason="helper_failed", stderr=tail)
        return None
    return r.stdout


def _cloak_fetch_html(url: str, *, timeout: int) -> tuple[bytes | None, str | None]:
    """Fetch HTML bytes through CloakBrowser / Playwright fallback. Returns (bytes, final_url)."""
    left = _time_left()
    if left is not None and left < 15:
        return None, None
    py = _resolve_cloak_python()
    if not py:
        return None, None
    helper = Path(__file__).resolve().with_name("cloak_pdf.py")
    if not helper.exists():
        return None, None
    try:
        cloak_timeout = max(15, min(_bounded_timeout(timeout), 45))
        r = subprocess.run(
            [py, str(helper), url, str(cloak_timeout), "--allow-html"],
            capture_output=True,
            timeout=cloak_timeout + 5,
        )
    except Exception:
        return None, None
    if r.returncode != 0 or not r.stdout:
        return None, None
    
    final_url = url
    if r.stderr:
        match = re.search(r"final_url='([^']+)'", r.stderr.decode("utf-8", "ignore"))
        if match:
            final_url = match.group(1)
    return r.stdout, final_url


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Universal URL safety check — applied in every mode.

    Returns (ok, reason). Blocks SSRF vectors regardless of whether the
    hostname would pass the allowlist check:
      - non-http(s) schemes (file://, ftp://, gopher://, etc.)
      - non-80/443 ports
      - IP literals in private / loopback / link-local / reserved /
        unspecified space
      - known cloud metadata hostnames
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False, "malformed_url"
    if parsed.scheme not in ("http", "https"):
        return False, "scheme_not_allowed"
    if parsed.port is not None and parsed.port not in (80, 443):
        return False, "port_not_allowed"
    host = (parsed.hostname or "").lower()
    if not host:
        return False, "empty_host"
    try:
        ip = ipaddress.ip_address(host)
        # Keep this address-class set in sync with _host_addrs_safe(); is_unspecified
        # blocks the 0.0.0.0 / :: literals, which route to localhost on many stacks.
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False, "private_ip"
    except ValueError:
        pass  # hostname is a name, not a literal — fine
    if host in _BLOCKED_HOSTS:
        return False, "blocked_host"
    return True, ""


def _host_addrs_safe(host: str) -> tuple[bool, str]:
    """Resolve `host` and reject if any address is in private space.

    `_is_safe_url` only inspects the literal host, so a public-looking
    hostname that resolves to a loopback / private / link-local / reserved
    address (the classic DNS-based SSRF vector) slips through. This closes
    that gap by checking every address the name resolves to. Fails closed:
    a name we cannot resolve is treated as not allowed.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False, "dns_error"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False, "private_ip"
    return True, ""


def _url_fetch_allowed(url: str) -> tuple[bool, str]:
    """Full pre-fetch gate: syntactic SSRF check + DNS-resolution check."""
    ok, reason = _is_safe_url(url)
    if not ok:
        return False, reason
    return _host_addrs_safe(urllib.parse.urlparse(url).hostname or "")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-run the SSRF gate on every redirect target.

    urllib follows 3xx redirects transparently, so a download URL that passes
    the initial check can still be bounced to loopback / a private host / cloud
    metadata. Validate each hop and refuse to follow an unsafe one.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, reason = _url_fetch_allowed(newurl)
        if not ok:
            raise urllib.error.HTTPError(newurl, code, f"unsafe_redirect:{reason}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Install process-wide so every urllib.request.urlopen() — PDF downloads and
# API calls alike — validates redirect hops. Tests patch urlopen directly, so
# this opener is bypassed there and stays inert in hermetic runs.
# The institutional proxy is published to the standard variables before the
# opener is built, so the metadata lookups go through it as well.
institutional.apply_environment()
urllib.request.install_opener(urllib.request.build_opener(_SafeRedirectHandler()))


# Simple per-process token bucket. Single-threaded, so no locking needed.
_last_request_monotonic: float = 0.0
_rate_lock = threading.Lock()


def _rate_limit_gate() -> None:
    """Enforce INSTITUTIONAL_RATE_PER_SEC pacing. No-op in public mode.

    Runs before every outbound HTTP request in institutional mode so
    that a single process cannot inadvertently hammer a publisher's
    servers beyond the configured rate.
    """
    global _last_request_monotonic
    if not _is_institutional():
        return
    with _rate_lock:
        min_interval = 1.0 / INSTITUTIONAL_RATE_PER_SEC
        now = time.monotonic()
        wait = _last_request_monotonic + min_interval - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _last_request_monotonic = now



# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

# Global output state (set by main()).
_format = "json"
_pretty = False
_stream = False
_request_id = ""
_started_monotonic = 0.0


_item_stats = threading.local()


def _stats_reset() -> None:
    _item_stats.data = {}


def _stats_record(source: str, *, ms: float, ok: bool, error: str | None = None) -> None:
    """Per-source attempt tally for this item (feeds the run dashboard)."""
    data = getattr(_item_stats, "data", None)
    if data is None:
        data = {}
        _item_stats.data = data
    entry = data.setdefault(source, {"attempts": 0, "ok": 0, "failed": 0, "ms": 0.0, "last_error": None})
    entry["attempts"] += 1
    entry["ms"] += ms
    if ok:
        entry["ok"] += 1
    else:
        entry["failed"] += 1
        if error:
            entry["last_error"] = str(error)


def _stats_snapshot() -> dict:
    return {k: dict(v) for k, v in (getattr(_item_stats, "data", None) or {}).items()}


def _now_ms() -> int:
    return int((time.monotonic() - _started_monotonic) * 1000)


def _log_text(msg: str) -> None:
    """Human-readable diagnostic → stderr only (used in text mode)."""
    if _format == "silent":
        return
    print(msg, file=sys.stderr, flush=True)


def _progress(event: str, **fields) -> None:
    """Progress event on stderr.

    JSON mode emits NDJSON so orchestrators can parse stderr for liveness.
    Text mode emits prose for humans.
    Silent mode emits nothing.
    """
    if _format == "silent":
        return
    if _format == "json":
        payload = {"event": event, "request_id": _request_id, "elapsed_ms": _now_ms(), **fields}
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)
        return

    # Text mode — render a short human line.
    if event == "session":
        # Agent-only diagnostic; silent in human mode.
        return
    if event == "start":
        _log_text(f"==> {fields.get('doi', '?')}")
    elif event == "source_skip":
        _log_text(f"  [{fields.get('source', '?')}] skipped ({fields.get('reason', '?')})")
    elif event == "source_try":
        _log_text(f"  [{fields.get('source', '?')}] trying…")
    elif event == "source_hit":
        _log_text(f"  [{fields.get('source', '?')}] {fields.get('pdf_url', '?')}")
    elif event == "source_miss":
        _log_text(f"  [{fields.get('source', '?')}] no PDF")
    elif event == "download_error":
        reason = fields.get("reason", "?")
        status = fields.get("http_status")
        detail = fields.get("error")
        if status:
            _log_text(f"  download failed: {reason} (HTTP {status})")
        elif detail:
            _log_text(f"  download failed: {reason} ({detail})")
        else:
            _log_text(f"  download failed: {reason}")
    elif event == "download_ok":
        _log_text(f"  saved → {fields.get('file', '?')}")
    elif event == "download_skip":
        _log_text(f"  [skip-existing] {fields.get('file', '?')}")
    elif event == "dry_run":
        _log_text(f"  [dry-run] [{fields.get('source', '?')}] {fields.get('pdf_url', '?')} → {fields.get('file', '?')}")
    elif event == "not_found":
        _log_text(f"  no OA PDF found for {fields.get('doi', '?')}")
    else:
        # fall back
        _log_text(f"  [{event}] {fields}")


def _dump_json(obj: dict) -> str:
    if _pretty:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    return json.dumps(obj, ensure_ascii=False)


def _emit(obj: dict) -> None:
    """Final result → stdout as JSON or human-readable text."""
    if _format == "json":
        print(_dump_json(obj))
    else:
        _emit_text(obj)


def _emit_ndjson(obj: dict) -> None:
    """Per-item streaming line on stdout (--stream mode)."""
    print(_dump_json(obj), flush=True)


def _emit_text(obj: dict) -> None:
    """Render a result envelope as human-readable text on stdout."""
    ok = obj.get("ok")
    if ok is False:
        err = obj.get("error", {})
        print(f"error: [{err.get('code', '?')}] {err.get('message', '?')}")
        return

    data = obj.get("data", {})
    results = data.get("results", [data] if "doi" in data else [])
    for r in results:
        if r.get("skipped"):
            status = "skipped"
        elif r.get("dry_run"):
            status = "dry-run"
        elif r.get("success"):
            status = "saved"
        else:
            status = "failed"
        src = r.get("source") or "?"
        doi = r.get("doi", "?")
        target = r.get("file") or r.get("pdf_url") or "?"
        print(f"[{src}] {doi} → {target}  ({status})")
    summary = data.get("summary")
    if summary:
        print(f"\n{summary['succeeded']}/{summary['total']} succeeded  ({summary.get('failed', 0)} failed)")
    nxt = data.get("next") or []
    if nxt:
        print("\nnext:")
        for hint in nxt:
            print(f"  {hint}")


def _meta(extra: dict | None = None) -> dict:
    m = {
        "request_id": _request_id,
        "latency_ms": _now_ms(),
        "schema_version": SCHEMA_VERSION,
        "cli_version": CLI_VERSION,
        "auth_mode": _auth_mode(),
    }
    if extra:
        m.update(extra)
    return m


def _envelope_ok(data: dict, *, ok=True, meta_extra: dict | None = None) -> dict:
    return {"ok": ok, "data": data, "meta": _meta(meta_extra)}


def _envelope_err(code: str, message: str, *, retryable: bool = False, **ctx) -> dict:
    e = {"code": code, "message": message, "retryable": retryable}
    e.update(ctx)
    return {"ok": False, "error": e, "meta": _meta()}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Per-item time budget (set by run_parallel for each worker thread)
# ---------------------------------------------------------------------------

_item_budget = threading.local()


def set_item_deadline(seconds: float | None) -> None:
    """Give the current thread's article a wall-clock budget (None clears it)."""
    _item_budget.deadline = (time.monotonic() + seconds) if seconds else None


def _time_left() -> float | None:
    deadline = getattr(_item_budget, "deadline", None)
    return None if deadline is None else deadline - time.monotonic()


def _deadline_exceeded() -> bool:
    left = _time_left()
    return left is not None and left <= 0


def _inherit_budget(fn):
    """Wrap ``fn`` so a pool thread runs under the submitting thread's budget."""
    deadline = getattr(_item_budget, "deadline", None)

    def run(*args, **kwargs):
        _item_budget.deadline = deadline
        try:
            return fn(*args, **kwargs)
        finally:
            _item_budget.deadline = None
    return run


def _bounded_timeout(timeout: float) -> int:
    """Clamp a per-request timeout to what is left of the item budget."""
    left = _time_left()
    if left is None:
        return int(timeout)
    return max(1, int(min(timeout, left)))


def _with_api_auth(url: str, headers: dict[str, str]) -> str:
    """Attach the operator's metadata-API keys to requests for those hosts.

    OpenAlex now meters anonymous use (a few cents/day shared per IP) and
    Semantic Scholar throttles the anonymous pool hard, so without a key both
    silently fail after the first few hundred lookups of a run.
    """
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "api.openalex.org":
        key = os.environ.get("OPENALEX_API_KEY", "").strip()
        if key and "api_key=" not in url:
            url += ("&" if "?" in url else "?") + "api_key=" + urllib.parse.quote(key)
    elif host == "api.semanticscholar.org":
        key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
        if key:
            headers["x-api-key"] = key
    return url


def _get(url: str, *, accept: str = "application/json", timeout: int, user_agent: str | None = None, ssl_context: ssl.SSLContext | None = None) -> bytes:
    if _deadline_exceeded():
        raise TimeoutError("item_deadline")
    timeout = _bounded_timeout(timeout)
    if _is_institutional():
        _rate_limit_gate()
    headers = {"User-Agent": user_agent or UA, "Accept": accept}
    url = _with_api_auth(url, headers)
    # A host that just failed repeatedly is skipped instead of being asked
    # again on every remaining article of the run.
    if not http_retry.host_ready(url):
        raise TimeoutError(f"host_cooldown:{http_retry.host_of(url)}")
    req = urllib.request.Request(url, headers=headers)
    last_err = None
    for attempt in range(http_retry.DEFAULT_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as r:
                data = r.read()
            http_retry.note_success(url)
            return data
        except urllib.error.HTTPError as e:
            # 401/403/404/410 are answers, not hiccups: never retried.
            if e.code not in http_retry.RETRYABLE_STATUS:
                raise
            retry_after = http_retry.parse_retry_after((e.headers or {}).get("Retry-After"))
            cooldown = http_retry.note_failure(url, status=e.code, retry_after=retry_after)
            last_err = e
            if cooldown or attempt == http_retry.DEFAULT_ATTEMPTS - 1 or _deadline_exceeded():
                break
            time.sleep(http_retry.backoff_delay(attempt, retry_after=retry_after))
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as e:
            cooldown = http_retry.note_failure(url)
            last_err = e
            if cooldown or attempt == http_retry.DEFAULT_ATTEMPTS - 1 or _deadline_exceeded():
                break
            time.sleep(http_retry.backoff_delay(attempt))
    if last_err is not None:
        raise last_err
    raise TimeoutError(f"Request timed out for {url}")


def _get_json(url: str, *, timeout: int):
    return json.loads(_get(url, timeout=timeout).decode("utf-8"))


def _scihub_rate_gate() -> None:
    """1 req/s pacing for Sci-Hub fetches, applied in every auth mode."""
    global _last_scihub_request_monotonic
    min_interval = 1.0 / SCIHUB_RATE_PER_SEC
    now = time.monotonic()
    wait = _last_scihub_request_monotonic + min_interval - now
    if wait > 0:
        time.sleep(wait)
        now = time.monotonic()
    _last_scihub_request_monotonic = now


def _is_allowed_host(url: str) -> bool:
    """Gatekeeper for any outbound PDF fetch.

    Only SSRF defense applies — private IPs, non-http(s) schemes, non-80/443
    ports, and cloud metadata hostnames are rejected. Everything else is
    allowed: the skill trusts URLs returned by the OA APIs it already called
    (Unpaywall, Semantic Scholar, bioRxiv, PMC), and the %PDF magic-byte +
    50 MB size checks in `_download` catch tampered responses.
    """
    ok, _reason = _is_safe_url(url)
    return ok


_INSTITUTIONAL_RETRY_ERRORS = ("http_401", "http_403", "http_402", "not_a_pdf")


def _institutional_retry_warranted(last_error) -> bool:
    """True for the failures a subscription would plausibly fix."""
    return bool(last_error) and str(last_error).startswith(_INSTITUTIONAL_RETRY_ERRORS)


_TRANSIENT_ERROR_RE = re.compile(r"http_(408|425|429|5\d\d)")


def _note_transient(url: str, error: str | None) -> None:
    """Feed the per-host throttle with a failure reported as a string."""
    if not error:
        return
    match = _TRANSIENT_ERROR_RE.search(str(error))
    if match:
        http_retry.note_failure(url, status=int(match.group(1)))
    elif "timeout" in str(error).lower() or "network" in str(error).lower():
        http_retry.note_failure(url)


def _download(url: str, dest: Path, *, timeout: int, _visited: set | None = None) -> str | None:
    """Download a PDF with GoByPASS403 multi-module bypass and stealth fallback."""
    if _deadline_exceeded():
        return "item_deadline"
    timeout = _bounded_timeout(timeout)
    visited = _visited if _visited is not None else set()
    if url in visited or len(visited) >= 8:
        return "landing_page_limit"
    visited.add(url)
    allowed, deny_reason = _url_fetch_allowed(url)
    if not allowed:
        _progress("download_error", reason="host_not_allowed", url=url, detail=deny_reason)
        return "host_not_allowed"

    remaining = http_retry.cooldown_remaining(url)
    if remaining > 0:
        _progress("download_skip_cooldown", url=url, host=http_retry.host_of(url), seconds=round(remaining, 1))
        return "host_cooldown"

    def _finalize(data: bytes) -> str | None:
        """Validate (%PDF magic, complete EOF trailer, pypdf syntax check, size cap) and write."""
        valid, clean_data, reason = validate_pdf_data(data)
        if not valid:
            _progress("download_error", reason=reason)
            return reason
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_dest = dest.with_name(f".{dest.name}.tmp.{os.getpid()}_{uuid.uuid4().hex[:6]}")
            tmp_dest.write_bytes(clean_data)
            tmp_dest.replace(dest)
        except OSError as e:
            _progress("download_error", reason="io_error", error=str(e))
            return "io_error"
        return None

    # 1. Bounded HTTP transport with per-host concurrency (curl_cffi/Chrome TLS).
    ok, err = bypass_download_pdf(url, dest, timeout=timeout)
    if ok:
        _progress("download_bypass403_ok", url=url)
        http_retry.note_success(url)
        return None
    _note_transient(url, err)

    # 2. Fallback Attempt: Standard urllib (different stack; can succeed on
    #    plain hosts where the primary transport hit a transient error).
    _rate_limit_gate()
    parsed = urllib.parse.urlparse(url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"

    headers = {
        "User-Agent": DOWNLOAD_UA,
        "Accept": "application/pdf,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Referer": referer,
    }

    last_error = err
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(MAX_PDF_SIZE + 1)
            if not data.startswith(b"%PDF") and len(data) <= 2 * 1024 * 1024:
                html_text = data.decode("utf-8", "replace")
                for candidate in extract_pdf_links(html_text, r.geturl()):
                    candidate_error = _download(candidate, dest, timeout=timeout, _visited=visited)
                    if candidate_error is None:
                        return None
            fin_res = _finalize(data)
            if fin_res is None:
                return None
            last_error = fin_res
    except urllib.error.HTTPError as e:
        last_error = f"http_{e.code}"
        if e.code in http_retry.RETRYABLE_STATUS:
            http_retry.note_failure(
                url, status=e.code,
                retry_after=http_retry.parse_retry_after((e.headers or {}).get("Retry-After")),
            )
    except Exception as e:
        last_error = str(e)
        http_retry.note_failure(url)

    # 3. Institutional access (campus proxy / EZproxy), for the refusals it
    #    is meant to solve: the paper is licensed, the anonymous request is not.
    if _institutional_retry_warranted(last_error) and institutional.is_configured():
        data, inst_error = institutional.fetch_pdf(url, timeout=timeout)
        if data:
            _progress("download_institutional_ok", url=url)
            fin_res = _finalize(data)
            if fin_res is None:
                return None
            last_error = fin_res
        else:
            _progress("download_institutional_miss", url=url, reason=inst_error)

    # 4. Headless browser, for repository pages that build their download
    #    link in JavaScript. Allowlisted institutional hosts only.
    if browser_fetch.is_enabled() and browser_fetch.host_allowed(url) and _institutional_retry_warranted(last_error):
        data, browser_error = browser_fetch.fetch_pdf(url, timeout=timeout)
        if data:
            _progress("download_browser_ok", url=url)
            fin_res = _finalize(data)
            if fin_res is None:
                return None
            last_error = fin_res
        else:
            _progress("download_browser_miss", url=url, reason=browser_error)

    # Handle the last error we encountered
    if last_error == "not_a_pdf":
        _progress("download_error", reason="not_a_pdf", url=url)
        return "not_a_pdf"
    elif last_error == "size_exceeded":
        return "size_exceeded"
    elif last_error == "io_error":
        return "io_error"
    elif last_error:
        _progress("download_error", reason="network_error", url=url, error=str(last_error))
        if str(last_error).startswith("http_"):
            return str(last_error)
        if "timeout" in str(last_error).lower() or "timed out" in str(last_error).lower():
            return "timeout"
        return "network_error"
    else:
        _progress("download_error", reason="network_error", url=url)
        return "network_error"

# Filename helpers
# ---------------------------------------------------------------------------


def _slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s[:n]


_JOURNAL_STOPWORDS = {"the", "of", "and", "for", "in", "on", "a", "an", "to", "&"}


def _journal_abbrev(name: str | None, max_len: int = 20) -> str:
    """ISO-style initials for 3+ words (PNAS, JACS, NEJM); CamelCase otherwise."""
    if not name:
        return ""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name) if w and w.lower() not in _JOURNAL_STOPWORDS]
    if not words:
        return ""
    if len(words) >= 3:
        return "".join(w[0].upper() for w in words)[:max_len]
    return "".join(w[:1].upper() + w[1:] for w in words)[:max_len]


def _filename(meta: dict) -> str:
    author = _slug((meta.get("author") or "unknown").split()[-1], 20)
    year = str(meta.get("year") or "nd")
    journal = _journal_abbrev(meta.get("journal"))
    title = _slug(meta.get("title") or "paper", 40)
    parts = [author, year]
    if journal:
        parts.append(journal)
    parts.append(title)
    return "_".join(parts) + ".pdf"


# ---------------------------------------------------------------------------
# Source resolvers
# ---------------------------------------------------------------------------


def _is_transport_exc(exc: Exception) -> bool:
    """True if `exc` is a retryable transport failure rather than a genuine miss.

    A 404/410 from a resolver means the paper isn't indexed at that source — a
    real miss a retry won't fix. Anything else (timeout, connection reset, 5xx,
    403, malformed JSON) is a transport-class failure: the source might well
    have the paper, we just couldn't reach it, so it should not be reported as a
    permanent ``not_found``.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in (404, 410):
        return False
    return True














PMC_S3_BUCKET_URL = "https://pmc-oa-opendata.s3.amazonaws.com"
_PMC_S3_KEY_RE = re.compile(r"<Key>(PMC\d+\.(\d+)/PMC\d+\.\d+\.pdf)</Key>")


















_PMCID_URL_RE = re.compile(r"/pmc/articles/(PMC\d+)", re.IGNORECASE)


def _pmcid_from_url(url: str | None) -> str | None:
    """Extract a PMCID from a URL like https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123/...

    S2's openAccessPdf.url often points to a PMC article without also
    populating externalIds.PubMedCentral; parsing the URL recovers the id
    so we can still build Europe PMC / PMC fallback candidates.
    """
    if not url:
        return None
    m = _PMCID_URL_RE.search(url)
    return m.group(1).upper() if m else None




# ---------------------------------------------------------------------------
# Title → DOI resolvers (Crossref + Semantic Scholar fallback)
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# Publisher-direct fallback (institutional mode only)
# ---------------------------------------------------------------------------
# When the five OA sources all miss and the operator has opted into
# institutional mode, construct a publisher-side PDF URL by DOI prefix.
# The caller's IP / subscription cookies / EZproxy determine whether the
# publisher actually serves the PDF; unauthorized responses (401/403 or an
# HTML login page) fail the %PDF magic-byte check and the envelope surfaces
# download_not_a_pdf. SSRF + 50 MB + 1 req/s rate limit still apply.

_PUBLISHER_DIRECT_TEMPLATES: dict[str, tuple[str, str]] = {
    # DOI prefix -> (publisher label, URL template).
    # {doi} = full DOI; {suffix} = part after the prefix.
    "10.1038/": ("nature", "https://www.nature.com/articles/{suffix}.pdf"),
    "10.1126/": ("science", "https://www.science.org/doi/pdf/{doi}"),
    "10.1002/": ("wiley", "https://onlinelibrary.wiley.com/doi/pdf/{doi}"),
    "10.1007/": ("springer", "https://link.springer.com/content/pdf/{doi}.pdf"),
    "10.1021/": ("acs", "https://pubs.acs.org/doi/pdf/{doi}"),
    "10.1073/": ("pnas", "https://www.pnas.org/doi/pdf/{doi}"),
    "10.1056/": ("nejm", "https://www.nejm.org/doi/pdf/{doi}"),
    "10.1177/": ("sage", "https://journals.sagepub.com/doi/pdf/{doi}"),
    "10.1080/": ("tandf", "https://www.tandfonline.com/doi/pdf/{doi}"),
    # PLOS serves every journal's PDF through the plosone path.
    "10.1371/": ("plos", "https://journals.plos.org/plosone/article/file?id={doi}&type=printable"),
    # Frontiers and PeerJ are fully open access (CC-BY).
    "10.3389/": ("frontiers", "https://www.frontiersin.org/articles/{doi}/pdf"),
    # 10.1016/ (Elsevier / Cell Press) needs PII lookup — handled separately below.
    # 10.3390/ (MDPI) needs slug lookup — handled separately below; the
    # canonical www.mdpi.com PDF URL is gated by Akamai and 403s many
    # data-center / non-Western IPs even on OA papers, so we route via the
    # pub.mdpi-res.com CDN instead (see _mdpi_pdf_candidates).
}


# MDPI uses a short journal abbreviation in its DOI suffix (e.g. "app" for
# Applied Sciences) but a longer slug in the CDN URL (e.g. "applsci"). For
# many journals these are identical — ijms, molecules, sensors, cells,
# nutrients, cancers, foods, plants, etc. — and the fallback below covers
# them. Only journals whose slug differs from the short need to live here.
# Source: MDPI's own pub.mdpi-res.com URL convention, verified against
# representative DOIs from each listed journal.
_MDPI_SHORT_TO_SLUG: dict[str, str] = {
    "app": "applsci",
    "su": "sustainability",
    "ma": "materials",
    "en": "energies",
    "ani": "animals",
    "polym": "polymers",
    "antiox": "antioxidants",
    "math": "mathematics",
    "sym": "symmetry",
    "nano": "nanomaterials",
    "met": "metals",
    "catal": "catalysts",
    "cryst": "crystals",
    "atmos": "atmosphere",
    "info": "information",
    "md": "marinedrugs",
    "fi": "futureinternet",
    "f": "forests",
    "w": "water",
    "v": "viruses",
    "d": "diversity",
}

# DOI suffix shape for MDPI: <journal-short><volume><issue:2><article:4|5>,
# e.g. jmmp9030084 = volume 9, issue 03, article 0084 and
# ijms242015170 = volume 24, issue 20, article 15170. The volume width varies,
# so the split is ambiguous; the Crossref record (volume + article number)
# settles it and the DOI-only guesses are the fallback.
_MDPI_DOI_SUFFIX_RE = re.compile(r"^([a-z]+)(\d+)$")


def _mdpi_volume_article_guesses(digits: str) -> list[tuple[int, int]]:
    guesses = []
    for art_len in (4, 5):
        vol_len = len(digits) - 2 - art_len
        if vol_len < 1:
            continue
        vol, art = int(digits[:vol_len]), int(digits[-art_len:])
        if vol and art and (vol, art) not in guesses:
            guesses.append((vol, art))
    return guesses


def _mdpi_pdf_candidates(doi: str, *, timeout: int = 8) -> list[str]:
    """CDN URL candidates for an MDPI DOI (10.3390/...).

    www.mdpi.com answers automated clients with a 403, but the same PDFs are
    on the mdpi-res.com CDN at <slug>/<slug>-<vol:02>-<art:05>/article_deploy/.
    Volume and article number come from Crossref when it has them, otherwise
    from decoding the DOI suffix. Empty list for non-MDPI or odd-shaped DOIs.
    """
    if not doi.lower().startswith("10.3390/"):
        return []
    m = _MDPI_DOI_SUFFIX_RE.match(doi[len("10.3390/"):].lower())
    if not m:
        return []
    short, digits = m.groups()
    pairs: list[tuple[int, int]] = []
    slugs: list[str] = []
    mapped = _MDPI_SHORT_TO_SLUG.get(short)
    if mapped:
        slugs.append(mapped)
    slugs.append(short)
    try:
        msg = _get_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}", timeout=timeout).get("message") or {}
        number = str(msg.get("article-number") or (msg.get("page") or "").split("-")[0])
        if str(msg.get("volume") or "").isdigit() and number.isdigit():
            pairs.append((int(msg["volume"]), int(number)))
        journal = re.sub(r"[^a-z0-9]", "", ((msg.get("container-title") or [""])[0]).lower())
        if journal and journal not in slugs:
            slugs.append(journal)
    except Exception:
        pass
    for guess in _mdpi_volume_article_guesses(digits):
        if guess not in pairs:
            pairs.append(guess)
    urls: list[str] = []
    for vol, art in pairs:
        for slug in dict.fromkeys(slugs):
            name = f"{slug}-{vol:02d}-{art:05d}"
            urls.append(f"https://pub.mdpi-res.com/{slug}/{name}/article_deploy/{name}.pdf")
    return urls[:6]


def _try_publisher_direct(doi: str, *, timeout: int) -> list[tuple[str, str]]:
    """Construct publisher-side direct PDF URL candidates by DOI prefix.

    Returns a list of (url, publisher_label) tuples in priority order, or
    an empty list if no template matches. Multiple candidates are returned
    when the publisher has more than one viable host (e.g. MDPI with both
    a mapped slug and a fallback slug). The actual HTTP fetch will reveal
    authorization failures via 401/403 or HTML responses.
    """
    if doi.startswith("10.1016/"):
        # Elsevier / ScienceDirect: Support official API with ELSEVIER_API_KEY + PII resolution
        els_key = os.environ.get("ELSEVIER_API_KEY", "").strip()
        # An un-entitled key refuses every article identically; once that is
        # established the API is skipped and only the public routes are kept.
        els_key_usable = bool(els_key) and credentialed_api_usable("https://api.elsevier.com/")
        candidates: list[tuple[str, str]] = []
        if els_key_usable:
            candidates.append((f"https://api.elsevier.com/content/article/doi/{urllib.parse.quote(doi)}?httpAccept=application/pdf&apiKey={els_key}", "elsevier_api"))

        try:
            data = _get_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}", timeout=timeout)
        except Exception:
            data = {}
        ids = (data.get("message") or {}).get("alternative-id") or []
        pii = next(
            (i for i in ids if isinstance(i, str) and i.startswith("S") and len(i) >= 16),
            None,
        )
        if pii:
            if els_key_usable:
                candidates.append((f"https://api.elsevier.com/content/article/pii/{pii}?httpAccept=application/pdf&apiKey={els_key}", "elsevier_api"))
            candidates.append((f"https://www.sciencedirect.com/science/article/pii/{pii}/pdfft", "elsevier"))

        if candidates:
            return candidates

    # Springer Nature: official OA fulltext API (legitimate, uses the operator's key).
    if doi.startswith(("10.1007/", "10.1038/", "10.1186/", "10.1140/")):
        spr_key = os.environ.get("SPRINGER_OA_API_KEY", "").strip() or os.environ.get("SPRINGER_API_KEY", "").strip()
        candidates = []
        if spr_key:
            try:
                q = urllib.parse.urlencode({"q": f"doi:{doi}", "api_key": spr_key})
                data = _get_json(f"https://api.springernature.com/openaccess/json?{q}", timeout=min(timeout, 8))
                for rec in (data.get("records") or []):
                    for url_entry in (rec.get("url") or []):
                        val = url_entry.get("value") if isinstance(url_entry, dict) else None
                        if val and (url_entry.get("format") == "pdf" or val.endswith(".pdf")):
                            candidates.append((val, "springer_api"))
            except Exception:
                pass
        candidates.append((f"https://link.springer.com/content/pdf/{urllib.parse.quote(doi)}.pdf", "springer"))
        return candidates

    # Wiley Text and Data Mining: legitimate only with a TDM token.
    if doi.startswith(("10.1002/", "10.1111/", "10.1046/")):
        wiley_token = os.environ.get("WILEY_TDM_TOKEN", "").strip()
        if wiley_token:
            return [(f"https://api.wiley.com/onlinelibrary/tdm/v1/articles/{urllib.parse.quote(doi)}", "wiley_tdm")]

    # PeerJ: 10.7717/peerj.16678 -> /articles/16678.pdf;
    #        10.7717/peerj-cs.1234 -> /articles/cs-1234.pdf
    if doi.startswith("10.7717/"):
        m = re.match(r"^peerj(?:-([a-z]+))?\.(\d+)$", doi[len("10.7717/"):].lower())
        if m:
            section, number = m.groups()
            slug = f"{section}-{number}" if section else number
            return [(f"https://peerj.com/articles/{slug}.pdf", "peerj")]

    if doi.startswith("10.3390/"):
        candidates = [(url, "mdpi") for url in _mdpi_pdf_candidates(doi, timeout=min(timeout, 8))]
        # The canonical page is Akamai-gated for many clients, but it is the
        # only route when the CDN path cannot be derived, so it goes last.
        candidates.append((f"https://www.mdpi.com/{urllib.parse.quote(doi[len('10.3390/'):])}/pdf", "mdpi"))
        return candidates

    for prefix, (label, tmpl) in _PUBLISHER_DIRECT_TEMPLATES.items():
        if doi.startswith(prefix):
            suffix = doi[len(prefix):]
            return [(tmpl.format(doi=doi, suffix=suffix), label)]

    return []


# ---------------------------------------------------------------------------
# Sci-Hub resolver
# ---------------------------------------------------------------------------

_SCIHUB_DISCOVERY_RE = re.compile(
    r'href=["\']https?://(?:www\.)?(sci-hub\.[a-z0-9.-]+)/?["\']',
    re.IGNORECASE,
)
# Phrases that signal the paper is genuinely not in Sci-Hub's corpus
# (vs. CAPTCHA / mirror outage). Lets us short-circuit instead of cycling
# through every mirror.
_SCIHUB_NOT_IN_CORPUS_PATTERNS = (
    re.compile(r"please\s+try\s+to\s+search\s+again\s+using\s+doi", re.IGNORECASE),
    re.compile(r"статья\s+не\s+найдена\s+в\s+базе", re.IGNORECASE),
    re.compile(r"article\s+not\s+found\s+in\s+(?:the\s+)?database", re.IGNORECASE),
)

# Lazily populated; reset only on process restart.
_scihub_discovered_cache: list[str] | None = None

# ---------------------------------------------------------------------------
# Sci-Hub circuit breaker
# ---------------------------------------------------------------------------
# After SCIHUB_CIRCUIT_BREAKER_THRESHOLD consecutive download_network_error
# events the source is disabled for the rest of the process.  Counts are
# updated under the GIL so no lock is needed for the simple int/bool
# operations used here.
SCIHUB_CIRCUIT_BREAKER_THRESHOLD: int = 5
_scihub_consecutive_failures: int = 0
_scihub_circuit_open: bool = False  # True → source disabled this run


def _is_scihub_enabled() -> bool:
    """True unless operator opted out via PAPER_FETCH_NO_SCIHUB=1 or the
    circuit breaker has tripped for this process run."""
    if _scihub_circuit_open:
        return False
    return not os.environ.get("PAPER_FETCH_NO_SCIHUB")


def _scihub_mirrors() -> list[str]:
    """Mirror list for Sci-Hub, in priority order.

    PAPER_FETCH_SCIHUB_MIRRORS (comma-sep) overrides the built-in defaults.
    Discovery (re-scanning SCIHUB_DISCOVERY_URL) is invoked separately by
    `try_scihub` after the configured list is exhausted.
    """
    override = os.environ.get("PAPER_FETCH_SCIHUB_MIRRORS", "").strip()
    if override:
        return _parse_mirror_overrides(override)
    return list(SCIHUB_DEFAULT_MIRRORS)


def _parse_mirror_overrides(raw: str) -> list[str]:
    """Parse comma-separated mirror overrides into bare hostnames.

    Accepts forms like ``sci-hub.ru``, ``https://sci-hub.ru``, or
    ``sci-hub.ru/path/`` and returns just the hostname. Empty / unsafe
    entries (non-http(s) schemes, IP literals in private space, blocked
    hosts) are dropped — without this, a typo in the env var could route
    traffic at an attacker-controlled host.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw_entry in raw.split(","):
        entry = raw_entry.strip().rstrip("/")
        if not entry:
            continue
        # Add a scheme so urlparse splits hostname correctly for bare
        # ``sci-hub.ru`` inputs (urlparse treats them as path-only).
        candidate = entry if "://" in entry else "https://" + entry
        try:
            parsed = urllib.parse.urlparse(candidate)
        except ValueError:
            continue
        if parsed.scheme not in ("http", "https"):
            continue
        host = (parsed.hostname or "").lower()
        if not host or host in seen:
            continue
        # Reuse the universal SSRF guard so an override of e.g.
        # ``localhost`` or a private IP literal is dropped.
        ok, _ = _is_safe_url(f"https://{host}/")
        if not ok:
            continue
        seen.add(host)
        out.append(host)
    return out


def _scihub_is_not_in_corpus(html: str) -> bool:
    """True if the HTML matches a known 'paper not in database' message.

    Lets the resolver skip the remaining mirrors when continuing is pointless
    (every mirror serves the same shared corpus). Distinct from CAPTCHA, which
    looks like an empty or challenge page — for that we still rotate mirrors.
    """
    return any(p.search(html) for p in _SCIHUB_NOT_IN_CORPUS_PATTERNS)


class _ScihubEmbedFinder(html.parser.HTMLParser):
    """Collect <iframe>/<embed> tags from Sci-Hub paper pages.

    Order-independent attribute capture — unlike the prior regex, an
    ``<iframe src="..." id="pdf">`` is treated identically to
    ``<iframe id="pdf" src="...">``. Records all candidates so the caller
    can prefer ``id="pdf"`` and fall back to any ``.pdf`` src.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # list of (id_attr_lower, src_attr) tuples, in document order.
        self.candidates: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self._maybe_record(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        # Self-closing variant (<embed ... />) — still want to capture.
        self._maybe_record(tag, attrs)

    def _maybe_record(self, tag: str, attrs: list) -> None:
        if tag.lower() not in ("iframe", "embed"):
            return
        attr_map = {(k or "").lower(): (v or "") for k, v in attrs}
        src = attr_map.get("src", "").strip()
        if not src:
            return
        self.candidates.append((attr_map.get("id", "").lower(), src))


def _scihub_normalize_pdf_url(url: str, mirror_host: str | None) -> str | None:
    """Normalize a candidate src into an absolute https URL.

    Returns None if the URL is path-relative without a mirror context to
    anchor it against — the caller will fall back to another mirror.
    """
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        if not mirror_host:
            return None
        return f"https://{mirror_host}{url}"
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def _scihub_extract_iframe(html_text: str, mirror_host: str | None = None) -> str | None:
    """Extract the embedded PDF URL from a Sci-Hub paper page.

    Sci-Hub returns an HTML page with an <iframe src="...pdf"> (or sometimes
    an <embed src="...pdf">) pointing at the actual PDF on a CDN. Returns
    the absolute https:// URL, or None if no embed found (CAPTCHA, missing
    paper, or layout change). When `mirror_host` is provided, path-relative
    URLs (e.g. `/downloads/abc.pdf`) are resolved against it.
    """
    finder = _ScihubEmbedFinder()
    try:
        finder.feed(html_text)
    except Exception:
        # Malformed markup — bail to None so the caller rotates mirrors.
        return None

    # Prefer tags carrying id="pdf" regardless of attribute order in the source.
    # Within each tier, prefer entries whose src contains ".pdf".
    pdf_id = [(i, s) for i, s in finder.candidates if i == "pdf"]
    other = [(i, s) for i, s in finder.candidates if i != "pdf"]

    for tier in (pdf_id, other):
        # First pass within tier — strict ".pdf" hint.
        for _, src in tier:
            if ".pdf" not in src.lower():
                continue
            normalized = _scihub_normalize_pdf_url(src.strip(), mirror_host)
            if normalized:
                return normalized
        # Second pass within the id="pdf" tier — Sci-Hub sometimes serves
        # an obfuscated CDN URL without the ``.pdf`` extension. Trust the
        # explicit id anchor over filename hints.
        if tier is pdf_id:
            for _, src in tier:
                normalized = _scihub_normalize_pdf_url(src.strip(), mirror_host)
                if normalized:
                    return normalized
    return None


def _scihub_discover_mirrors(*, timeout: int) -> list[str]:
    """Scrape SCIHUB_DISCOVERY_URL for current mirror list. Cached per process."""
    global _scihub_discovered_cache
    if _scihub_discovered_cache is not None:
        return _scihub_discovered_cache
    try:
        html = _get(SCIHUB_DISCOVERY_URL, accept="text/html", timeout=timeout).decode("utf-8", "replace")
    except Exception as e:
        _progress("scihub_discover_failed", reason=str(e))
        _scihub_discovered_cache = []
        return []
    found: list[str] = []
    seen: set[str] = set()
    for m in _SCIHUB_DISCOVERY_RE.finditer(html):
        host = m.group(1).lower()
        if host in seen:
            continue
        seen.add(host)
        found.append(host)
    _scihub_discovered_cache = found
    if found:
        _progress("scihub_discover_ok", mirrors=found)
    return found






# Download failures worth retrying from the Internet Archive's copy: the live
# host refused or lost the file, not "this URL was never a PDF".
_WAYBACK_RETRY_REASONS = ("http_403", "http_404", "http_410", "http_429", "http_5", "network_error", "timeout", "not_a_pdf")
WAYBACK_MAX_URLS = 4


_wayback_lock = threading.Lock()
_wayback_next_at = 0.0
_wayback_paused_until = 0.0
WAYBACK_MIN_INTERVAL = 1.0      # archive.org answers bursts with HTTP 429
WAYBACK_429_PAUSE = 300.0




_osti_lock = threading.Lock()
_osti_failures = 0
OSTI_MAX_FAILURES = 3   # consecutive transport failures before OSTI is skipped for the run






def _annas_archive_mirrors() -> list[str]:
    """Mirror list for Anna's Archive, in priority order."""
    override = os.environ.get("PAPER_FETCH_ANNAS_ARCHIVE_MIRRORS", "").strip()
    if override:
        return [m.strip().rstrip("/") for m in override.split(",") if m.strip()]
    return list(ANNAS_ARCHIVE_DEFAULT_MIRRORS)










# ---------------------------------------------------------------------------
# Libgen resolver
# ---------------------------------------------------------------------------


def _is_libgen_enabled() -> bool:
    """True unless operator opted out via PAPER_FETCH_NO_LIBGEN=1."""
    return not os.environ.get("PAPER_FETCH_NO_LIBGEN")


def _libgen_mirrors() -> list[str]:
    """Mirror list for Libgen in priority order."""
    override = os.environ.get("PAPER_FETCH_LIBGEN_MIRRORS", "").strip()
    if override:
        return [m.strip().rstrip("/") for m in override.split(",") if m.strip()]
    return list(LIBGEN_DEFAULT_MIRRORS)




# ---------------------------------------------------------------------------
# Crossref direct links resolver
# ---------------------------------------------------------------------------




# Crossref lists text-mining links for every article, including ones that can
# only answer an error without the publisher credential they require, or that
# never carry a PDF at all. Asking for those spends a request per article and
# returns 400 every time.
_TDM_CREDENTIAL_HOSTS = {
    "api.wiley.com": "WILEY_TDM_TOKEN",
    "api.elsevier.com": "ELSEVIER_API_KEY",
}

# ---------------------------------------------------------------------------
# Entitlement circuit breaker
# ---------------------------------------------------------------------------
# A publisher API key can be perfectly valid and still be entitled to nothing:
# Elsevier, for instance, answers 403 AUTHENTICATION_ERROR for every article
# when the key is not bound to a subscribing institution. That verdict depends
# on the credential, not on the DOI, so it will be identical for every article
# in the run. After a few identical refusals the route is dropped rather than
# re-asked once per article — 43 guaranteed-403 requests in one run is the
# measured cost of not doing this.

CREDENTIAL_GIVE_UP_AFTER = 3

# Refusals that indict the credential. A timeout or a 5xx says nothing about
# entitlement and must never trip the breaker.
_CREDENTIAL_REFUSALS = ("http_401", "http_403", "http_402")

_credential_refusals: dict[str, int] = {}
_credential_lock = threading.Lock()


def _credentialed_host(url: str) -> str | None:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host if host in _TDM_CREDENTIAL_HOSTS else None


def note_credential_refusal(url: str, reason: str | None) -> None:
    """Record a refusal from a credentialed publisher API."""
    host = _credentialed_host(url)
    if not host or not str(reason or "").startswith(_CREDENTIAL_REFUSALS):
        return
    with _credential_lock:
        _credential_refusals[host] = _credential_refusals.get(host, 0) + 1
        if _credential_refusals[host] == CREDENTIAL_GIVE_UP_AFTER:
            _progress(
                "source_skip", source=host,
                reason="credential_not_entitled",
                detail=f"{CREDENTIAL_GIVE_UP_AFTER} refusals in a row; dropping this route for the rest of the run",
            )


def credentialed_api_usable(url: str) -> bool:
    """False once a publisher API has proved its key is entitled to nothing."""
    host = _credentialed_host(url)
    if not host:
        return True
    with _credential_lock:
        return _credential_refusals.get(host, 0) < CREDENTIAL_GIVE_UP_AFTER


def reset_credential_refusals() -> None:
    with _credential_lock:
        _credential_refusals.clear()


def _crossref_link_is_futile(url: str, content_type: str) -> bool:
    """True for a Crossref link that cannot produce a PDF for this operator."""
    if content_type.startswith(("text/plain", "text/html", "application/xml", "text/xml")):
        return True
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    required = _TDM_CREDENTIAL_HOSTS.get(host)
    return bool(required and not os.environ.get(required, "").strip())


# ---------------------------------------------------------------------------
# ACL Anthology & PaperDL resolvers
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Post-download bibliographic identity gate
# ---------------------------------------------------------------------------
#
# A structurally valid PDF (right magic bytes, parseable, under the size cap)
# is not evidence that it is the RIGHT PDF. Every candidate that reaches disk
# must additionally prove it corresponds to the requested article before the
# pipeline is allowed to call it a success. See identity.py for the actual
# comparison logic; this section wires it into the download path and persists
# the verdict alongside the file so the cache layer never has to re-trust a
# file blindly (see _read_identity_sidecar / _write_identity_sidecar).


def _identity_sidecar_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".identity.json")


def _read_identity_sidecar(dest: Path) -> dict | None:
    """Stored identity verdict for a cached PDF, or None to re-validate it.

    A record written by an older validator is ignored: the gate has since
    grown stricter, and files it accepted must be re-checked rather than
    trusted on the strength of that earlier verdict.
    """
    try:
        record = json.loads(_identity_sidecar_path(dest).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(record, dict) or record.get("validator_version") != _identity.VALIDATOR_VERSION:
        return None
    return record


def _write_identity_sidecar(dest: Path, record: dict) -> None:
    try:
        _identity_sidecar_path(dest).write_text(
            json.dumps({**record, "validator_version": _identity.VALIDATOR_VERSION}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _quarantine_unvalidated_file(dest: Path) -> None:
    """Rename a cached PDF that failed (re)validation instead of deleting it.

    Non-destructive: the operator can inspect it later. It is renamed out of
    the way so no future glob-based cache lookup can pick it back up and
    silently hand it out as if it were the requested article.
    """
    try:
        quarantined = dest.with_name(dest.stem + ".INVALID_IDENTITY" + dest.suffix)
        if quarantined.exists():
            quarantined.unlink()
        dest.rename(quarantined)
    except OSError:
        pass
    try:
        _identity_sidecar_path(dest).unlink(missing_ok=True)
    except OSError:
        pass


def _find_cached_pdf_for_doi(out_dir: Path, doi: str) -> Path | None:
    """Locate a previously downloaded PDF that claims to belong to ``doi``.

    Filenames are built from bibliographic metadata (author_year_journal_title
    — see _filename()), never from the DOI itself, so "does this DOI already
    have a file on disk" cannot be answered by string-matching the DOI against
    filenames. The identity sidecar written by _validate_downloaded_file() is
    the authoritative doi->file mapping; a legacy filename-substring match is
    kept only as a last-resort candidate finder for pre-migration downloads,
    and is never trusted without going through the identity gate first.
    """
    target = normalize_doi(doi)
    if not target or not out_dir.exists():
        return None

    for sidecar in out_dir.glob("*.identity.json"):
        try:
            record = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            continue
        stored_doi = normalize_doi((record.get("expected") or {}).get("doi") or "")
        if stored_doi == target:
            pdf_path = sidecar.with_name(sidecar.name[: -len(".identity.json")])
            if pdf_path.is_file():
                return pdf_path

    # Legacy fallback: pre-migration files have no sidecar at all. A doi-slug
    # substring match is a weak *candidate finder* only — the caller always
    # runs it through the identity gate before treating it as a hit.
    doi_slug = _slug(doi, n=20).lower()
    if doi_slug:
        for existing in out_dir.glob("*.pdf"):
            if doi_slug in existing.name.lower() and not existing.name.endswith(".INVALID_IDENTITY.pdf"):
                return existing
    return None


def _identity_result_fields(verdict: dict) -> dict:
    """Common identity fields attached to any result envelope (success or cache hit)."""
    return {
        "identity_validated": verdict["identity_validated"],
        "validation_method": verdict["validation_method"],
        "validation_score": verdict["validation_score"],
        "expected_title": (verdict.get("expected") or {}).get("title"),
        "detected_title": verdict.get("detected_title"),
        "expected_doi": (verdict.get("expected") or {}).get("doi"),
        "detected_doi": verdict.get("detected_doi"),
        "sha256": verdict.get("sha256"),
    }


def _expected_identity(doi: str, meta: dict) -> dict:
    meta = meta or {}
    return {
        "doi": doi,
        "title": meta.get("title"),
        "author": meta.get("author"),
        "journal": meta.get("journal"),
        "year": _extract_first_year(meta),
    }


def _validate_downloaded_file(dest: Path, *, expected: dict, record_doi_matched: bool) -> dict:
    """Run the identity gate against a file already written to disk.

    Returns the full verdict from identity.validate_article_identity(),
    plus sha256/validated_at bookkeeping. Never raises: a PDF that cannot be
    parsed for text (scanned/corrupted) simply yields no PDF-side evidence,
    which validate_article_identity() already treats as "fall back to
    record-level / bibliographic evidence".
    """
    try:
        data = dest.read_bytes()
    except OSError:
        data = b""
    pdf_identity = _identity.extract_pdf_identity(data)
    verdict = _identity.validate_article_identity(
        expected, pdf_identity=pdf_identity, record_doi_matched=record_doi_matched,
    )
    verdict["sha256"] = _identity.sha256_hex(data) if data else None
    verdict["validated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    verdict["expected"] = expected
    return verdict


# ---------------------------------------------------------------------------
# Core fetch logic
# ---------------------------------------------------------------------------


def _download_failure(
    doi: str,
    meta: dict,
    sources_tried: list[str],
    errors: list[dict],
    *,
    candidates: list[tuple[str, str]] | None = None,
) -> dict:
    """Build a per-item download failure result. `errors` must be non-empty."""
    last = errors[-1]
    # `host_cooldown` is the most retryable reason of all: the article was
    # never actually requested, it was skipped because that host had just
    # refused somebody else. By the end of a run the cooldown is long gone.
    retryable = last["reason"] in (
        "host_cooldown", "network_error", "timeout", "http_429", "size_exceeded", "io_error",
    ) or str(last["reason"]).startswith("http_5")
    code = f"download_{last['reason']}"
    err_obj = {
        "code": code,
        "message": (
            f"All {len(errors)} candidate(s) failed; last error from {last['source']}: {last['reason']}"
            if len(errors) > 1
            else f"Download failed from {last['source']}: {last['reason']}"
        ),
        "retryable": retryable,
    }
    if retryable and code in RETRY_AFTER_HOURS:
        err_obj["retry_after_hours"] = RETRY_AFTER_HOURS[code]
    out = {
        "doi": doi,
        "success": False,
        "source": last["source"],
        "pdf_url": last["url"],
        "file": None,
        "meta": meta or {},
        "sources_tried": sources_tried,
        "download_attempts": errors,
        "error": err_obj,
    }
    if candidates:
        out["candidates"] = [{"source": s, "url": u} for s, u in candidates]
    return out


def fetch(
    doi: str,
    out_dir: Path,
    *,
    dry_run: bool,
    overwrite: bool,
    timeout: int,
    sources: list[str] | None = None,
) -> dict:
    """Resolve and optionally download a single DOI.

    Returns a structured per-item result (not an envelope). Guaranteed keys:
      doi, success, source, pdf_url, file, meta, sources_tried, error?
    """
    doi = doi.strip()
    for _prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi.org/", "dx.doi.org/", "doi:"):
        if doi.startswith(_prefix):
            doi = doi[len(_prefix):]
            break
    # Strip URL fragments — e.g. "10.1002/xyz#ch3" from a browser copy-paste
    if "#" in doi:
        doi_stripped = doi.split("#")[0]
        if doi_stripped != doi:
            print(f"[fetch] DOI fragment stripped: {doi!r} → {doi_stripped!r}", file=sys.stderr)
        doi = doi_stripped
    if not _DOI_RE.match(doi):
        return {
            "doi": doi,
            "success": False,
            "source": "unknown",
            "pdf_url": None,
            "file": None,
            "meta": {},
            "sources_tried": [],
            "error": {
                "code": "validation_error",
                "message": f"Not a valid DOI: {doi!r} (expected pattern {DOI_PATTERN})",
                "retryable": False,
            },
        }

    # Pre-check: reuse a previously downloaded PDF only after confirming (or
    # re-confirming) its bibliographic identity — a cache hit is never
    # trusted purely because a file with a plausible name exists on disk
    # (see _find_cached_pdf_for_doi / _validate_downloaded_file).
    if not overwrite and out_dir.exists():
        existing = _find_cached_pdf_for_doi(out_dir, doi)
        if existing is not None:
            cached = _read_identity_sidecar(existing)
            if cached is None:
                cached = _validate_downloaded_file(
                    existing, expected=_expected_identity(doi, {}), record_doi_matched=False,
                )
                if cached["identity_validated"]:
                    _write_identity_sidecar(existing, cached)
            if cached["identity_validated"]:
                _progress("download_skip", doi=doi, file=str(existing))
                return {
                    "doi": doi,
                    "success": True,
                    "source": "cache",
                    "pdf_url": None,
                    "file": str(existing),
                    "meta": {"title": doi},
                    "sources_tried": [],
                    "skipped": True,
                    "skip_reason": "file_exists",
                    "identity_validated": True,
                    "validation_method": cached["validation_method"],
                    "validation_score": cached["validation_score"],
                }
            _progress(
                "validation_rejected", doi=doi, source="cache", reason=cached.get("reason"),
                detected_doi=cached.get("detected_doi"), phase="pre_check_revalidation",
            )
            _quarantine_unvalidated_file(existing)
            # Fall through to the full source search below — a stale/wrong
            # cached file must never short-circuit a fresh, correct download.

    _stats_reset()
    _progress("start", doi=doi)

    # Source filtering for multi-session / worker isolation
    sources_allowed = set(s.strip().lower() for s in sources) if sources else None
    env_sources = os.environ.get("PAPER_FETCH_SOURCES")
    if not sources_allowed and env_sources:
        sources_allowed = set(s.strip().lower() for s in env_sources.split(",") if s.strip())

    def _can_try(src_name: str) -> bool:
        if sources_allowed is None:
            return True
        return src_name.lower() in sources_allowed

    sources_tried: list[str] = []
    meta: dict = {}
    download_errors: list[dict] = []
    resolver_errors: list[dict] = []
    got_useful_metadata: bool = False
    source_details: dict[str, dict] = {}
    attempted_urls: set[str] = set()
    candidates: list[tuple[str, str]] = []
    identity_rejections: list[dict] = []

    FATAL_DL_ERRORS = ("io_error",)

    def _merge_meta(extra: dict) -> list[str]:
        added: list[str] = []
        for k, v in (extra or {}).items():
            if v and not meta.get(k):
                meta[k] = v
                added.append(k)
        return added

    # --- Semantic Scholar is queried lazily (cached) ---
    _s2_cache: dict | None = None

    def _get_s2() -> tuple[str | None, dict, dict]:
        nonlocal _s2_cache
        if _s2_cache is not None:
            return _s2_cache["pdf"], _s2_cache["meta"], _s2_cache["ext"]
        if not _can_try("semantic_scholar"):
            return None, {}, {}
        if "semantic_scholar" not in sources_tried:
            sources_tried.append("semantic_scholar")
        _progress("source_try", doi=doi, source="semantic_scholar")
        pdf, s2_meta, ext = try_semantic_scholar(doi, timeout=timeout, errors=resolver_errors)
        _s2_cache = {"pdf": pdf, "meta": s2_meta, "ext": ext}
        return pdf, s2_meta, ext

    def _success(src: str, url: str, extra: dict | None = None) -> dict:
        fname = _filename(meta or {"title": doi})
        dest = out_dir / fname
        out = {
            "doi": doi,
            "success": True,
            "source": src,
            "pdf_url": url,
            "file": str(dest),
            "meta": meta or {},
            "sources_tried": sources_tried,
        }
        if src in source_details:
            out["source_detail"] = source_details[src]
        if url in _CLOAK_DOWNLOADS:
            out["via"] = "cloak"
        if candidates:
            out["candidates"] = [{"source": s, "url": u} for s, u in candidates]
        if extra:
            out.update(extra)
        return out

    _identity_extra = _identity_result_fields

    def _try_candidate(
        cand_src: str, cand_url: str, cand_detail: dict | None = None, *, record_doi_matched: bool = True,
    ) -> tuple[typing.Any, bool]:
        """Try downloading a candidate URL immediately. Returns (result_dict, is_fatal).

        A download that lands a structurally valid PDF is not yet a success:
        the file must additionally pass validate_article_identity() before
        this returns anything other than a miss (None, False), so the caller
        moves on to the next candidate exactly as it would for a 404.
        """
        if not cand_url or cand_url in attempted_urls:
            return None, False
        # Hard per-article budget: once it is gone, abort the whole item
        # (fatal) so the cascade stops between candidates instead of walking
        # every remaining source and mirror.
        if _deadline_exceeded():
            return _download_failure(doi, meta, sources_tried, download_errors, candidates=candidates), True
        attempted_urls.add(cand_url)
        candidates.append((cand_src, cand_url))
        if cand_detail:
            source_details[cand_src] = cand_detail

        fname = _filename(meta or {"title": doi})
        dest = out_dir / fname
        expected = _expected_identity(doi, meta)

        if dry_run:
            _progress("dry_run", doi=doi, source=cand_src, pdf_url=cand_url, file=str(dest))
            return _success(cand_src, cand_url, {"dry_run": True}), False

        if dest.exists() and not overwrite:
            cached = _read_identity_sidecar(dest)
            if cached is None:
                # Pre-existing file with no identity record (legacy download,
                # or a file dropped in by another tool): it must be validated
                # now rather than trusted just because a PDF is present.
                cached = _validate_downloaded_file(dest, expected=expected, record_doi_matched=record_doi_matched)
                if cached["identity_validated"]:
                    _write_identity_sidecar(dest, cached)
            if cached["identity_validated"]:
                _progress("download_skip", doi=doi, file=str(dest))
                return _success(cand_src, cand_url, {"skipped": True, "skip_reason": "file_exists", **_identity_extra(cached)}), False
            _progress(
                "validation_rejected", doi=doi, source=cand_src, url=cand_url,
                reason=cached.get("reason"), detected_doi=cached.get("detected_doi"), phase="cache_revalidation",
            )
            _quarantine_unvalidated_file(dest)
            identity_rejections.append({"source": cand_src, "url": cand_url, **cached})
            # Fall through: dest no longer exists, so a fresh download is attempted below.

        # Sci-Hub CDN (sci.bban.top) blocks automated access entirely; using
        # the full timeout (25 s) × ~7 bypass403 sub-attempts = ~175 s wasted
        # per DOI.  Cap scihub candidates at 9 s so that bypass403 still gets
        # a fair chance on each sub-attempt without stalling a worker.
        _dl_timeout = 9 if cand_src == "scihub" else timeout
        _attempt_started = time.monotonic()
        dl_err = _download(cand_url, dest, timeout=_dl_timeout)
        _attempt_ms = (time.monotonic() - _attempt_started) * 1000
        if dl_err is None:
            verdict = _validate_downloaded_file(dest, expected=expected, record_doi_matched=record_doi_matched)
            _progress(
                "validation_result", doi=doi, source=cand_src, url=cand_url,
                result="CONFIRMED" if verdict["identity_validated"] else "REJECTED",
                method=verdict["validation_method"], expected_doi=doi, detected_doi=verdict.get("detected_doi"),
                expected_title=expected.get("title"), detected_title=verdict.get("detected_title"),
            )
            if not verdict["identity_validated"]:
                _stats_record(cand_src, ms=_attempt_ms, ok=False, error="identity_rejected")
                identity_rejections.append({"source": cand_src, "url": cand_url, **verdict})
                try:
                    dest.unlink()
                except OSError:
                    pass
                return None, False
            _write_identity_sidecar(dest, verdict)
            _stats_record(cand_src, ms=_attempt_ms, ok=True)
            _progress("download_ok", doi=doi, file=str(dest), source=cand_src)
            return _success(cand_src, cand_url, _identity_extra(verdict)), False

        _stats_record(cand_src, ms=_attempt_ms, ok=False, error=dl_err)
        download_errors.append({"source": cand_src, "url": cand_url, "reason": dl_err})
        note_credential_refusal(cand_url, dl_err)
        if dl_err in FATAL_DL_ERRORS:
            return _download_failure(doi, meta, sources_tried, download_errors, candidates=candidates), True
        return None, False

    def _try_rendered_xml(
        cand_src: str, xml_url: str, cand_detail: dict | None = None,
    ) -> tuple[typing.Any, bool]:
        """Full-text XML turned into a PDF, then judged like any other file.

        The rendering carries the DOI on its first page, so it faces exactly
        the same identity gate as a publisher PDF — a wrong article cannot
        slip through just because it arrived as XML.
        """
        if not xml_url or xml_url in attempted_urls:
            return None, False
        if _deadline_exceeded():
            return _download_failure(doi, meta, sources_tried, download_errors, candidates=candidates), True
        attempted_urls.add(xml_url)
        candidates.append((cand_src, xml_url))
        if cand_detail:
            source_details[cand_src] = cand_detail

        dest = out_dir / _filename(meta or {"title": doi})
        if dry_run:
            _progress("dry_run", doi=doi, source=cand_src, pdf_url=xml_url, file=str(dest))
            return _success(cand_src, xml_url, {"dry_run": True}), False

        started = time.monotonic()
        xml_bytes = pmc_s3.fetch_xml(xml_url, timeout=timeout)
        if not xml_bytes:
            download_errors.append({"source": cand_src, "url": xml_url, "reason": "xml_fetch_failed"})
            _stats_record(cand_src, ms=(time.monotonic() - started) * 1000, ok=False, error="xml_fetch_failed")
            return None, False

        article = fulltext_xml.parse_fulltext(xml_bytes)
        if not article:
            # Abstract-only deposits land here; they are not full text.
            download_errors.append({"source": cand_src, "url": xml_url, "reason": "xml_not_full_text"})
            _stats_record(cand_src, ms=(time.monotonic() - started) * 1000, ok=False, error="xml_not_full_text")
            return None, False

        for field, value in (("title", article.get("title")),
                             ("author", (article.get("authors") or [None])[0])):
            if value and not meta.get(field):
                meta[field] = value
        dest = out_dir / _filename(meta or {"title": doi})

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            rendered = fulltext_xml.render_pdf(article, doi, dest)
        except Exception as exc:
            _progress("download_error", reason="xml_render_error", url=xml_url, error=str(exc))
            rendered = False
        if not rendered:
            download_errors.append({"source": cand_src, "url": xml_url, "reason": "xml_render_failed"})
            _stats_record(cand_src, ms=(time.monotonic() - started) * 1000, ok=False, error="xml_render_failed")
            return None, False

        elapsed = (time.monotonic() - started) * 1000
        # A rendered file must never survive on disk unvalidated: if the gate
        # itself fails, the file is discarded exactly as a rejection would be.
        try:
            # The PMCID came from NCBI's own ID converter, keyed by this DOI,
            # so the record-to-DOI link is authoritative rather than inferred.
            verdict = _validate_downloaded_file(
                dest, expected=_expected_identity(doi, meta), record_doi_matched=True,
            )
        except Exception as exc:
            _progress("download_error", reason="xml_validation_error", url=xml_url, error=str(exc))
            _stats_record(cand_src, ms=elapsed, ok=False, error="xml_validation_error")
            download_errors.append({"source": cand_src, "url": xml_url, "reason": "xml_validation_error"})
            dest.unlink(missing_ok=True)
            return None, False

        _progress(
            "validation_result", doi=doi, source=cand_src, url=xml_url,
            result="CONFIRMED" if verdict["identity_validated"] else "REJECTED",
            method=verdict["validation_method"], expected_doi=doi,
            detected_doi=verdict.get("detected_doi"),
        )
        if not verdict["identity_validated"]:
            _stats_record(cand_src, ms=elapsed, ok=False, error="identity_rejected")
            identity_rejections.append({"source": cand_src, "url": xml_url, **verdict})
            dest.unlink(missing_ok=True)
            return None, False

        _write_identity_sidecar(dest, verdict)
        _stats_record(cand_src, ms=elapsed, ok=True)
        _progress("download_ok", doi=doi, file=str(dest), source=cand_src)
        return _success(cand_src, xml_url, {"rendered_from": "jats_xml", **_identity_extra(verdict)}), False

    # -----------------------------------------------------------------------
    # 1. Unpaywall (fast OA lookup)
    # -----------------------------------------------------------------------
    up_url: str | None = None
    if EMAIL and _can_try("unpaywall"):
        _progress("source_try", doi=doi, source="unpaywall")
        sources_tried.append("unpaywall")
        up_url, up_meta = try_unpaywall(doi, timeout=timeout, errors=resolver_errors)
        if _merge_meta(up_meta):
            got_useful_metadata = True
        if up_url:
            _progress("source_hit", doi=doi, source="unpaywall", pdf_url=up_url)
            # Enrich metadata from S2 if author/title missing
            if not meta.get("author") or not meta.get("title"):
                _, s2_meta, _ = _get_s2()
                added = _merge_meta(s2_meta)
                if added:
                    got_useful_metadata = True
                    _progress("source_enrich", doi=doi, source="semantic_scholar", fields=added)
            for candidate in dict.fromkeys([up_url, *up_meta.get("pdf_candidates", [])]):
                res, fatal = _try_candidate("unpaywall", candidate)
                if res is not None or fatal:
                    return res
        else:
            _progress("source_miss", doi=doi, source="unpaywall")
    elif not EMAIL and _can_try("unpaywall"):
        _progress("source_skip", doi=doi, source="unpaywall", reason="UNPAYWALL_EMAIL not set")

    # -----------------------------------------------------------------------
    # 1b. PMC Open Access bucket on AWS.
    #
    # Placed this early because it is the cheapest reliable route we have for
    # biomedical DOIs: two unauthenticated requests, no rate limit and no
    # challenge page. Every other PMC surface now refuses plain clients — the
    # article pages answer a reCAPTCHA interstitial and oa.fcgi was retired —
    # so without this the whole PMC corpus is unreachable over HTTP.
    # -----------------------------------------------------------------------
    def _try_pmc_s3() -> tuple[typing.Any, bool]:
        """Article from the OA bucket, as a PDF or rendered from its JATS XML."""
        ids = try_pmc_idconv(doi, timeout=min(timeout, 10))
        pmcid = ids.get("pmcid")
        if not pmcid:
            _progress("source_miss", doi=doi, source="pmc_s3", reason="no_pmcid")
            return None, False
        if ids.get("pmid") and not meta.get("pmid"):
            meta["pmid"] = ids["pmid"]

        assets = pmc_s3.list_assets(pmcid, timeout=min(timeout, 15))
        if not assets["version"]:
            # Normal: author manuscripts (NIHMS) are on PMC but outside the
            # Open Access Subset, so they are simply not in the bucket.
            _progress("source_miss", doi=doi, source="pmc_s3", reason="not_in_oa_subset", pmcid=pmcid)
            return None, False

        detail = {"pmcid": pmcid, "version": assets["version"]}
        if assets["pdf"]:
            _progress("source_hit", doi=doi, source="pmc_s3", pdf_url=assets["pdf"], **detail)
            res, fatal = _try_candidate("pmc_s3", assets["pdf"], detail)
            if res is not None or fatal:
                return res, fatal

        # No PDF in the bucket (common — many deposits are XML-only). The JATS
        # full text is the same article, and for the triage step it is better
        # input than a scanned PDF, so it is rendered and used.
        if assets["xml"]:
            _progress("source_hit", doi=doi, source="pmc_s3_xml", pdf_url=assets["xml"], **detail)
            return _try_rendered_xml("pmc_s3_xml", assets["xml"], dict(detail, format="jats"))

        _progress("source_miss", doi=doi, source="pmc_s3", reason="no_usable_asset", pmcid=pmcid)
        return None, False

    if _can_try("pmc_s3"):
        _progress("source_try", doi=doi, source="pmc_s3")
        sources_tried.append("pmc_s3")
        _pmc_res, _pmc_fatal = _try_pmc_s3()
        if _pmc_res is not None or _pmc_fatal:
            return _pmc_res

    # -----------------------------------------------------------------------
    # 2. Semantic Scholar (OA PDF + external IDs)
    # -----------------------------------------------------------------------
    s2_pdf, s2_meta, ext = _get_s2()
    if _merge_meta(s2_meta):
        got_useful_metadata = True
    if s2_pdf and _can_try("semantic_scholar"):
        _progress("source_hit", doi=doi, source="semantic_scholar", pdf_url=s2_pdf)
        res, fatal = _try_candidate("semantic_scholar", s2_pdf)
        if res is not None:
            return res
        if fatal:
            return res
    elif not up_url and _can_try("semantic_scholar"):
        _progress("source_miss", doi=doi, source="semantic_scholar")

    # -----------------------------------------------------------------------
    # 3. OpenAlex
    # -----------------------------------------------------------------------
    if _can_try("openalex"):
        if "openalex" not in sources_tried:
            sources_tried.append("openalex")
        _progress("source_try", doi=doi, source="openalex")
        openalex_urls, openalex_meta = try_openalex(doi, timeout=timeout, errors=resolver_errors)
        added = _merge_meta(openalex_meta)
        if added:
            got_useful_metadata = True
            _progress("source_enrich", doi=doi, source="openalex", fields=added)
        if openalex_urls:
            for oa_url in openalex_urls:
                _progress("source_hit", doi=doi, source="openalex", pdf_url=oa_url)
                res, fatal = _try_candidate("openalex", oa_url)
                if res is not None:
                    return res
                if fatal:
                    return res
        else:
            _progress("source_miss", doi=doi, source="openalex")

    # -----------------------------------------------------------------------
    # 4. ArXiv
    # -----------------------------------------------------------------------
    if not ext.get("ArXiv") and doi.lower().startswith("10.48550/arxiv."):
        ext["ArXiv"] = doi[len("10.48550/arxiv."):]
        if not meta.get("title"):
            ax_meta = try_arxiv_metadata(ext["ArXiv"], timeout=timeout)
            if ax_meta:
                added = _merge_meta(ax_meta)
                if added:
                    got_useful_metadata = True
                    _progress("source_enrich", doi=doi, source="arxiv", fields=added)
            else:
                _progress("source_enrich_failed", doi=doi, source="arxiv")

    if ext.get("ArXiv") and _can_try("arxiv"):
        if "arxiv" not in sources_tried:
            sources_tried.append("arxiv")
        arxiv_url = try_arxiv(ext["ArXiv"])
        _progress("source_hit", doi=doi, source="arxiv", pdf_url=arxiv_url)
        res, fatal = _try_candidate("arxiv", arxiv_url)
        if res is not None:
            return res
        if fatal:
            return res

    # 4b. Sibling Semantic Scholar record (arXiv/repository copy), key only
    if not ext.get("ArXiv") and not s2_pdf and meta.get("title") and _can_try("semantic_scholar"):
        sib_url = try_semantic_scholar_copy_by_title(doi, meta["title"], timeout=timeout)
        if sib_url:
            _progress("source_hit", doi=doi, source="semantic_scholar", pdf_url=sib_url, via="title_search")
            res, fatal = _try_candidate("semantic_scholar", sib_url, record_doi_matched=False)
            if res is not None or fatal:
                return res

    # -----------------------------------------------------------------------
    # 5. Europe PMC / PubMed Central / PubMed
    # -----------------------------------------------------------------------
    if not ext.get("PubMedCentral"):
        for url_src in (up_url, s2_pdf):
            pmcid_from_url = _pmcid_from_url(url_src)
            if pmcid_from_url:
                ext["PubMedCentral"] = pmcid_from_url
                break

    # Europe PMC: OA full-text links it aggregates (publisher, repositories)
    # plus the PMCID when it has one.
    if _can_try("europe_pmc"):
        _progress("source_try", doi=doi, source="europe_pmc")
        if "europe_pmc" not in sources_tried:
            sources_tried.append("europe_pmc")
        epmc_urls, epmc_pmcid = try_europe_pmc_links(doi, timeout=timeout, errors=resolver_errors)
        if epmc_pmcid and not ext.get("PubMedCentral"):
            ext["PubMedCentral"] = epmc_pmcid
        for epmc_url in epmc_urls:
            _progress("source_hit", doi=doi, source="europe_pmc", pdf_url=epmc_url)
            res, fatal = _try_candidate("europe_pmc", epmc_url)
            if res is not None or fatal:
                return res
        if not epmc_urls and not epmc_pmcid:
            _progress("source_miss", doi=doi, source="europe_pmc")

    if not ext.get("PubMedCentral") and (_can_try("pmc") or _can_try("pubmed")):
        ids = try_pmc_idconv(doi, timeout=timeout)
        if ids.get("pmcid"):
            ext["PubMedCentral"] = ids["pmcid"]
        elif ids.get("pmid") and not ext.get("PMID"):
            ext["PMID"] = ids["pmid"]

    if not ext.get("PubMedCentral") and _can_try("pubmed"):
        pmid = ext.get("PMID") or ext.get("PubMed") or try_pmid(doi, timeout=timeout, errors=resolver_errors)
        if pmid:
            if "pubmed" not in sources_tried:
                sources_tried.append("pubmed")
            _progress("source_try", doi=doi, source="pubmed", pmid=pmid)
            pmcid = try_pmcid_from_pmid(pmid, timeout=timeout)
            if pmcid:
                ext["PubMedCentral"] = pmcid
                _progress("source_enrich", doi=doi, source="pubmed", fields=["PubMedCentral"])
            else:
                _progress("source_miss", doi=doi, source="pubmed", reason="pmcid_not_found")

    # PubMed Central, via the PMC Cloud Service bucket (the website itself
    # now serves a reCAPTCHA to automated clients).
    if ext.get("PubMedCentral") and (_can_try("pmc") or _can_try("pubmed")):
        if "pmc" not in sources_tried:
            sources_tried.append("pmc")
        _progress("source_try", doi=doi, source="pmc", pmcid=ext["PubMedCentral"])
        pmc_url = try_pmc(ext["PubMedCentral"], timeout=timeout)
        if pmc_url:
            _progress("source_hit", doi=doi, source="pmc", pdf_url=pmc_url)
            res, fatal = _try_candidate("pmc", pmc_url)
            if res is not None or fatal:
                return res
        else:
            _progress("source_miss", doi=doi, source="pmc", reason="not_in_pmc_open_data")
        epmc_render = try_europe_pmc(ext["PubMedCentral"])
        _progress("source_hit", doi=doi, source="europe_pmc", pdf_url=epmc_render)
        res, fatal = _try_candidate("europe_pmc", epmc_render)
        if res is not None or fatal:
            return res

    # -----------------------------------------------------------------------
    # 6. BioRxiv / MedRxiv
    # -----------------------------------------------------------------------
    if doi.startswith("10.1101/") and _can_try("biorxiv"):
        if "biorxiv" not in sources_tried:
            sources_tried.append("biorxiv")
        _progress("source_try", doi=doi, source="biorxiv")
        bx_url = try_biorxiv(doi, timeout=timeout)
        if bx_url:
            _progress("source_hit", doi=doi, source="biorxiv", pdf_url=bx_url)
            res, fatal = _try_candidate("biorxiv", bx_url)
            if res is not None:
                return res
            if fatal:
                return res
        else:
            _progress("source_miss", doi=doi, source="biorxiv")

    # -----------------------------------------------------------------------
    # 6b. ACL Anthology (Computational Linguistics / NLP papers)
    # -----------------------------------------------------------------------
    if "10.18653/" in doi.lower() and _can_try("acl_anthology"):
        if "acl_anthology" not in sources_tried:
            sources_tried.append("acl_anthology")
        _progress("source_try", doi=doi, source="acl_anthology")
        acl_urls, _ = try_acl_anthology(doi, timeout=timeout, errors=resolver_errors)
        if acl_urls:
            for acl_url in acl_urls:
                _progress("source_hit", doi=doi, source="acl_anthology", pdf_url=acl_url)
                res, fatal = _try_candidate("acl_anthology", acl_url)
                if res is not None:
                    return res
                if fatal:
                    return res
        else:
            _progress("source_miss", doi=doi, source="acl_anthology")

    # -----------------------------------------------------------------------
    # 7. CORE
    # -----------------------------------------------------------------------
    if _can_try("core"):
        if CORE_API_KEY and os.environ.get("PAPER_FETCH_SKIP_CORE") != "1":
            if "core" not in sources_tried:
                sources_tried.append("core")
            _progress("source_try", doi=doi, source="core")
            core_urls = try_core(doi, timeout=timeout, errors=resolver_errors)
            if core_urls:
                for core_url in core_urls:
                    _progress("source_hit", doi=doi, source="core", pdf_url=core_url)
                    res, fatal = _try_candidate("core", core_url)
                    if res is not None:
                        return res
                    if fatal:
                        return res
            else:
                _progress("source_miss", doi=doi, source="core")
        else:
            _progress("source_skip", doi=doi, source="core", reason="CORE_API_KEY not set" if not CORE_API_KEY else "PAPER_FETCH_SKIP_CORE=1")

    # -----------------------------------------------------------------------
    # 8. Libgen (Library Genesis)
    # -----------------------------------------------------------------------
    if _is_libgen_enabled() and _can_try("libgen"):
        if "libgen" not in sources_tried:
            sources_tried.append("libgen")
        _progress("source_try", doi=doi, source="libgen")
        libgen_urls, libgen_meta = try_libgen(doi, title=meta.get("title"), timeout=timeout, errors=resolver_errors)
        if _merge_meta(libgen_meta):
            got_useful_metadata = True
        if libgen_urls:
            for lg_url in libgen_urls:
                _progress("source_hit", doi=doi, source="libgen", pdf_url=lg_url)
                res, fatal = _try_candidate("libgen", lg_url, record_doi_matched=False)
                if res is not None:
                    return res
                if fatal:
                    return res
        else:
            _progress("source_miss", doi=doi, source="libgen")

    # -----------------------------------------------------------------------
    # 9. Crossref Direct Links
    # -----------------------------------------------------------------------
    if _can_try("crossref"):
        if "crossref" not in sources_tried:
            sources_tried.append("crossref")
        _progress("source_try", doi=doi, source="crossref")
        cr_urls, cr_meta = try_crossref_links(doi, timeout=timeout, errors=resolver_errors)
        if _merge_meta(cr_meta):
            got_useful_metadata = True
        if cr_urls:
            for cr_url in cr_urls:
                _progress("source_hit", doi=doi, source="crossref", pdf_url=cr_url)
                res, fatal = _try_candidate("crossref", cr_url)
                if res is not None:
                    return res
                if fatal:
                    return res
        else:
            _progress("source_miss", doi=doi, source="crossref")

    # -----------------------------------------------------------------------
    # 10. Publisher-direct fallback (institutional mode or Elsevier API key configured)
    # -----------------------------------------------------------------------
    els_key = os.environ.get("ELSEVIER_API_KEY", "").strip()
    # MDPI and PLOS are fully open access, so their direct routes need no institutional mode.
    if (_is_institutional() or (els_key and doi.startswith("10.1016/")) or doi.startswith(("10.3390/", "10.1371/", "10.3389/", "10.7717/"))) and _can_try("publisher_direct"):
        _progress("source_try", doi=doi, source="publisher_direct")
        pub_candidates = _try_publisher_direct(doi, timeout=timeout)
        if pub_candidates:
            if "publisher_direct" not in sources_tried:
                sources_tried.append("publisher_direct")
            for pub_url, pub_label in pub_candidates:
                _progress("source_hit", doi=doi, source="publisher_direct", pdf_url=pub_url, publisher=pub_label)
                res, fatal = _try_candidate("publisher_direct", pub_url)
                if res is not None:
                    return res
                if fatal:
                    return res
        else:
            _progress("source_miss", doi=doi, source="publisher_direct", reason="no_template_for_doi_prefix")

    # -----------------------------------------------------------------------
    # 10b. Publisher full-text XML (Elsevier / Springer APIs), rendered to PDF
    # -----------------------------------------------------------------------
    if _can_try("publisher_xml") and not dry_run and not _deadline_exceeded():
        _progress("source_try", doi=doi, source="publisher_xml")
        if "publisher_xml" not in sources_tried:
            sources_tried.append("publisher_xml")
        xml_dest = out_dir / _filename(meta or {"title": doi})
        xml_ok, xml_err = fulltext_xml.fetch_as_pdf(doi, xml_dest, timeout=min(timeout, 25))
        if xml_ok:
            verdict = _validate_downloaded_file(
                xml_dest, expected=_expected_identity(doi, meta), record_doi_matched=True,
            )
            _progress(
                "validation_result", doi=doi, source="publisher_xml",
                result="CONFIRMED" if verdict["identity_validated"] else "REJECTED",
                method=verdict["validation_method"],
            )
            if verdict["identity_validated"]:
                _write_identity_sidecar(xml_dest, verdict)
                _progress("download_ok", doi=doi, file=str(xml_dest), source="publisher_xml")
                return _success("publisher_xml", None, {"full_text_format": "xml", **_identity_extra(verdict)})
            identity_rejections.append({"source": "publisher_xml", "url": None, **verdict})
            try:
                xml_dest.unlink()
            except OSError:
                pass
        else:
            _progress("source_miss", doi=doi, source="publisher_xml", reason=xml_err)

    # -----------------------------------------------------------------------
    # 11. DOI resolver fallback
    # -----------------------------------------------------------------------
    if _can_try("doi_resolver"):
        _progress("source_try", doi=doi, source="doi_resolver")
        if "doi_resolver" not in sources_tried:
            sources_tried.append("doi_resolver")
        resolver_url, resolver_meta = try_doi_resolver(doi, timeout=timeout, errors=resolver_errors)
        if resolver_url:
            _progress("source_hit", doi=doi, source="doi_resolver", pdf_url=resolver_url)
            if _merge_meta(resolver_meta):
                got_useful_metadata = True
            for candidate in dict.fromkeys([resolver_url, *resolver_meta.get("pdf_candidates", [])]):
                res, fatal = _try_candidate("doi_resolver", candidate, record_doi_matched=False)
                if res is not None or fatal:
                    return res
        else:
            _progress("source_miss", doi=doi, source="doi_resolver")

    # -----------------------------------------------------------------------
    # 11b. Open Access Button (Aggregator API) fallback
    # -----------------------------------------------------------------------
    if _can_try("oa_button"):
        _progress("source_try", doi=doi, source="oa_button")
        if "oa_button" not in sources_tried:
            sources_tried.append("oa_button")
        oa_url = try_oa_button(doi, timeout=timeout, errors=resolver_errors)
        if oa_url:
            _progress("source_hit", doi=doi, source="oa_button", pdf_url=oa_url)
            res, fatal = _try_candidate("oa_button", oa_url)
            if res is not None:
                return res
            if fatal:
                return res
        else:
            _progress("source_miss", doi=doi, source="oa_button")

    # -----------------------------------------------------------------------
    # 11b2. OSTI (accepted manuscripts of US DOE-funded articles)
    # -----------------------------------------------------------------------
    if _can_try("osti") and not os.environ.get("PAPER_FETCH_NO_OSTI"):
        _progress("source_try", doi=doi, source="osti")
        if "osti" not in sources_tried:
            sources_tried.append("osti")
        osti_url = try_osti(doi, timeout=timeout)
        if osti_url:
            _progress("source_hit", doi=doi, source="osti", pdf_url=osti_url)
            res, fatal = _try_candidate("osti", osti_url, {"note": "accepted manuscript"})
            if res is not None or fatal:
                return res
        else:
            _progress("source_miss", doi=doi, source="osti")

    # -----------------------------------------------------------------------
    # 11c. OpenAlex Content API (cached PDF; needs OPENALEX_API_KEY)
    # -----------------------------------------------------------------------
    if _can_try("openalex_content") and os.environ.get("OPENALEX_API_KEY", "").strip():
        _progress("source_try", doi=doi, source="openalex_content")
        if "openalex_content" not in sources_tried:
            sources_tried.append("openalex_content")
        oac_url = try_openalex_content(doi, timeout=timeout, errors=resolver_errors)
        if oac_url:
            _progress("source_hit", doi=doi, source="openalex_content")
            res, fatal = _try_candidate("openalex_content", oac_url)
            if res is not None or fatal:
                return res
        else:
            _progress("source_miss", doi=doi, source="openalex_content")

    # -----------------------------------------------------------------------
    # 11d. Wayback Machine copies of OA URLs that failed live
    # -----------------------------------------------------------------------
    if _can_try("wayback") and not os.environ.get("PAPER_FETCH_NO_WAYBACK"):
        retry_urls = [
            e["url"] for e in download_errors
            if e.get("url") and e.get("source") not in ("scihub", "libgen", "annas_archive", "wayback")
            and any(str(e.get("reason", "")).startswith(r) for r in _WAYBACK_RETRY_REASONS)
        ]
        retry_urls = list(dict.fromkeys(retry_urls))[:WAYBACK_MAX_URLS]
        if retry_urls:
            _progress("source_try", doi=doi, source="wayback")
            if "wayback" not in sources_tried:
                sources_tried.append("wayback")
            hit = False
            for live_url in retry_urls:
                wb_url = try_wayback(live_url, timeout=timeout)
                if not wb_url:
                    continue
                hit = True
                _progress("source_hit", doi=doi, source="wayback", pdf_url=wb_url)
                res, fatal = _try_candidate("wayback", wb_url, {"original_url": live_url}, record_doi_matched=False)
                if res is not None or fatal:
                    return res
            if not hit:
                _progress("source_miss", doi=doi, source="wayback")

    # -----------------------------------------------------------------------
    # 12. Sci-Hub fallback
    # -----------------------------------------------------------------------
    if _is_scihub_enabled() and _can_try("scihub") and "scihub" not in sources_tried:
        _progress("source_try", doi=doi, source="scihub")
        sources_tried.append("scihub")
        sh_hit = try_scihub(doi, timeout=timeout)
        if sh_hit:
            sh_url, sh_mirror = sh_hit
            source_details["scihub"] = {"mirror": sh_mirror}
            _progress("source_hit", doi=doi, source="scihub", pdf_url=sh_url, mirror=sh_mirror)
            res, fatal = _try_candidate("scihub", sh_url, {"mirror": sh_mirror})
            if res is not None:
                # Successful download — reset consecutive failure counter.
                global _scihub_consecutive_failures
                _scihub_consecutive_failures = 0
                return res
            if fatal:
                return res
            # _try_candidate returned (None, False) — check if error was a
            # network error and update the circuit-breaker counter.
            last_sh_err = next(
                (e.get("reason", "") for e in reversed(download_errors) if e.get("source") == "scihub"),
                "",
            )
            if "network_error" in last_sh_err or "download_network_error" in last_sh_err:
                global _scihub_circuit_open
                _scihub_consecutive_failures += 1
                if _scihub_consecutive_failures >= SCIHUB_CIRCUIT_BREAKER_THRESHOLD and not _scihub_circuit_open:
                    _scihub_circuit_open = True
                    print(
                        f"\n⚡ [CIRCUIT BREAKER] Sci-Hub desativado após "
                        f"{_scihub_consecutive_failures} falhas de rede consecutivas. "
                        "Será reativado apenas ao reiniciar o processo.\n",
                        flush=True,
                    )
        else:
            _progress("source_miss", doi=doi, source="scihub")
            # No PDF URL found (CAPTCHA / not in corpus) also counts as a
            # failure for circuit-breaker purposes when every mirror returned
            # an error (try_scihub returns None on network-level failures too).

    # -----------------------------------------------------------------------
    # 13. Anna's Archive fallback
    # -----------------------------------------------------------------------
    if _can_try("annas_archive") and "annas_archive" not in sources_tried:
        _progress("source_try", doi=doi, source="annas_archive")
        sources_tried.append("annas_archive")
        aa_url = try_annas_archive(doi, timeout=timeout, errors=resolver_errors)
        if aa_url:
            _progress("source_hit", doi=doi, source="annas_archive", pdf_url=aa_url)
            res, fatal = _try_candidate("annas_archive", aa_url, record_doi_matched=False)
            if res is not None:
                return res
            if fatal:
                return res
        else:
            _progress("source_miss", doi=doi, source="annas_archive")

    # -----------------------------------------------------------------------
    # Final Exhaustion Handling
    # -----------------------------------------------------------------------
    # A candidate that downloaded a structurally valid PDF but failed the
    # bibliographic identity gate is a materially different outcome from
    # "no OA copy exists" or "every download attempt errored out" — it means
    # we found and fetched *something*, and correctly refused to call it a
    # match. Surface that distinctly rather than folding it into a generic
    # not_found, per the absolute rule: never report success (or a generic
    # miss) when a real, wrong-article PDF was the actual result.
    if identity_rejections:
        _progress("not_found", doi=doi, reason="article_identity_not_confirmed")
        return {
            "doi": doi,
            "success": False,
            "source": None,
            "pdf_url": None,
            "file": None,
            "meta": meta or {},
            "sources_tried": sources_tried,
            "identity_validated": False,
            "identity_rejections": identity_rejections,
            "error": {
                "code": "article_identity_not_confirmed",
                "message": (
                    f"{len(identity_rejections)} candidate PDF(s) were downloaded but rejected: "
                    "none could be confirmed as the requested article."
                ),
                "retryable": True,
                "retry_after_hours": RETRY_AFTER_HOURS["not_found"],
                "reason": "structurally valid PDF(s) found, but bibliographic identity did not match the requested DOI",
            },
        }

    if download_errors:
        return _download_failure(doi, meta, sources_tried, download_errors, candidates=candidates)

    if resolver_errors and not got_useful_metadata:
        _progress("resolve_error", doi=doi, sources=[e["source"] for e in resolver_errors])
        return {
            "doi": doi,
            "success": False,
            "source": None,
            "pdf_url": None,
            "file": None,
            "meta": meta or {},
            "sources_tried": sources_tried,
            "resolver_errors": resolver_errors,
            "error": {
                "code": "resolve_network_error",
                "message": "Metadata resolvers failed with transport errors; OA availability is unknown",
                "retryable": True,
                "retry_after_hours": RETRY_AFTER_HOURS["resolve_network_error"],
                "reason": "resolver API unreachable (timeout / 5xx / 403), not a confirmed absence of OA",
            },
        }

    _progress("not_found", doi=doi)
    err = {
        "code": "not_found",
        "message": "No open-access PDF found",
        "retryable": True,
        "retry_after_hours": RETRY_AFTER_HOURS["not_found"],
        "reason": "OA availability changes over time; retry after embargo lifts or preprint appears",
    }
    if not _is_institutional():
        err["suggest_institutional"] = True
        err["hint"] = (
            "If your institution has a subscription to this paper, "
            "set PAPER_FETCH_INSTITUTIONAL=1 and run from on-campus or VPN."
        )
    return {
        "doi": doi,
        "success": False,
        "source": None,
        "pdf_url": None,
        "file": None,
        "meta": meta or {},
        "sources_tried": sources_tried,
        "error": err,
    }



# ===========================================================================


# ---------------------------------------------------------------------------
# Idempotency sidecar
# ---------------------------------------------------------------------------


def _idem_path(out_dir: Path, key: str) -> Path:
    # Hash the raw key so distinct long keys that share an 80-char prefix don't
    # collide onto the same sidecar and replay each other's envelope.
    safe = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return out_dir / ".paper-fetch-idem" / f"{safe}.json"


def _idem_load(out_dir: Path, key: str) -> dict | None:
    p = _idem_path(out_dir, key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _idem_store(out_dir: Path, key: str, envelope: dict) -> None:
    p = _idem_path(out_dir, key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # best-effort only


# ---------------------------------------------------------------------------
# Schema subcommand
# ---------------------------------------------------------------------------


def build_schema() -> dict:
    return {
        "command": "paper-fetch",
        "cli_version": CLI_VERSION,
        "schema_version": SCHEMA_VERSION,
        "description": "Fetch PDFs by DOI via Unpaywall, Semantic Scholar, OpenAlex, arXiv, Europe PMC, PMC, PubMed, bioRxiv/medRxiv, CORE, Libgen, and Crossref. In institutional mode (PAPER_FETCH_INSTITUTIONAL=1), also attempts a publisher-direct fetch (publisher_direct source) using the caller's own subscription IP / cookies / EZproxy. As a last resort, falls back to Sci-Hub mirrors (scihub source) and Anna's Archive (annas_archive source). On finding a valid download candidate, downloads immediately and proceeds to the next paper.",
        "subcommands": {
            "schema": "Print this schema as JSON and exit (no network).",
        },
        "params": {
            "doi": {
                "type": "string",
                "required": False,
                "description": "DOI to fetch (positional). Use '-' to read DOIs line-by-line from stdin.",
                "pattern": DOI_PATTERN,
                "example": "10.1038/s41586-020-2649-2",
            },
            "title": {
                "type": "string",
                "required": False,
                "description": "Paper title; resolved to a DOI via Crossref before download. Mutually exclusive with positional DOI / --batch. The resolved DOI, top match, and up to 3 candidates are surfaced under meta.title_resolution.",
                "example": "Highly accurate protein structure prediction with AlphaFold",
            },
            "batch": {
                "type": "path",
                "required": False,
                "description": "File with one DOI per line for bulk download. Use '-' to read from stdin.",
            },
            "out": {
                "type": "path",
                "required": False,
                "default": "pdfs",
                "description": "Output directory.",
            },
            "dry_run": {
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "Resolve sources without downloading; preview the PDF URL and destination path.",
            },
            "format": {
                "type": "enum",
                "values": ["json", "text"],
                "required": False,
                "default": "auto (json when stdout not a TTY, text otherwise)",
                "description": "Output format. json for agents, text for humans.",
            },
            "pretty": {
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "Pretty-print JSON output with 2-space indentation.",
            },
            "stream": {
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "Emit one NDJSON result per line on stdout as each DOI resolves, then a final summary line.",
            },
            "overwrite": {
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "Re-download PDFs even when the destination file already exists.",
            },
            "idempotency_key": {
                "type": "string",
                "required": False,
                "description": "Stable key for safe retries. Re-running with the same key returns the original envelope from a sidecar in <out>/.paper-fetch-idem/.",
            },
            "timeout": {
                "type": "integer",
                "required": False,
                "default": DEFAULT_TIMEOUT,
                "description": "HTTP timeout in seconds per request.",
            },
        },
        "exit_codes": {
            "0": "success (all DOIs resolved / previewed)",
            "1": "unresolved (some DOIs had no OA copy; no transport failure)",
            "2": "reserved for auth errors (currently unused)",
            "3": "validation error (bad arguments, missing input)",
            "4": "transport error (network / download / IO failure; retryable class)",
        },
        "error_codes": {
            "validation_error": {"retryable": False, "message": "Bad arguments or empty input"},
            "not_found": {"retryable": True, "retry_after_hours": RETRY_AFTER_HOURS["not_found"], "message": "No OA PDF found anywhere; OA availability changes over time"},
            "resolve_network_error": {"retryable": True, "retry_after_hours": RETRY_AFTER_HOURS["resolve_network_error"], "message": "Metadata resolvers failed with transport errors (timeout / 5xx / 403); OA availability is unknown, retry rather than treating as not_found"},
            "title_resolve_failed": {"retryable": False, "message": "Crossref returned no items for the given title; provide a DOI directly or refine the title"},
            "download_network_error": {"retryable": True, "retry_after_hours": RETRY_AFTER_HOURS["download_network_error"], "message": "Network failure during download"},
            "download_not_a_pdf": {"retryable": False, "message": "Response was not a PDF (HTML landing page)"},
            "download_host_not_allowed": {"retryable": False, "message": "PDF URL failed SSRF safety check (private IP, non-http(s) scheme, non-80/443 port, or blocked metadata host)"},
            "download_size_exceeded": {"retryable": True, "retry_after_hours": RETRY_AFTER_HOURS["download_size_exceeded"], "message": f"Response exceeded {MAX_PDF_SIZE // (1024*1024)} MB limit"},
            "download_io_error": {"retryable": True, "retry_after_hours": RETRY_AFTER_HOURS["download_io_error"], "message": "Local filesystem write failed"},
            "internal_error": {"retryable": False, "message": "Unexpected error"},
        },
        "envelope": {
            "success": {"ok": True, "data": {"results": [], "summary": {}, "next": []}, "meta": {}},
            "partial": {"ok": "partial", "data": {"results": [], "summary": {}, "next": []}, "meta": {}},
            "failure": {"ok": False, "error": {"code": "", "message": "", "retryable": False}, "meta": {}},
        },
        "result_fields": {
            "source_detail": "Optional per-source diagnostics (e.g. {'mirror': 'sci-hub.ru'} when source='scihub'). Present only when the resolving source has additional context worth surfacing for orchestrator routing.",
            "via": "Optional. Set to 'cloak' when the PDF was fetched through the CloakBrowser fallback (a Cloudflare-blocked URL retried via stealth Chromium). Absent for ordinary downloads. Requires PAPER_FETCH_CLOAK.",
            "resolver_errors": "Optional. Present on a resolve_network_error result; lists the metadata resolvers that failed with a transport error (timeout / 5xx / 403) rather than a genuine 404 miss, as [{source, detail}].",
        },
        "deprecations": [],
        "meta_fields": {
            "request_id": "Unique per-invocation id; correlates stderr progress events with the stdout envelope.",
            "latency_ms": "Wall-clock time from process start to this emit.",
            "schema_version": "Version of this schema contract; bumped on any additive or breaking change.",
            "cli_version": "Version of the paper-fetch binary that produced the envelope.",
            "auth_mode": "Either 'public' (OA sources, no client rate limit) or 'institutional' (user opted in via PAPER_FETCH_INSTITUTIONAL=1; 1 req/s rate limit to protect the operator's IP from publisher-side throttling).",
            "sources_tried": "Union of sources consulted across all DOIs in this run.",
            "title_resolution": "Present only when --title was used. Includes: query, resolver (the resolver whose match was used: 'crossref' or 'semantic_scholar'), resolvers_tried (ordered list of every resolver consulted), resolved_doi, resolved_title, match_score (Crossref relevance score; absent for S2 matches), candidates (top-3 from the winning resolver), low_confidence (true if the chosen DOI failed the score/gap heuristics), low_confidence_reason ('score_below_threshold' / 'ambiguous_runner_up' / 'no_match'), fallback_reason (why Crossref's match was rejected when S2 was used), and crossref_candidates (top-3 Crossref hits when the S2 fallback won, for cross-resolver inspection). Agents should sanity-check the top match — especially when low_confidence is true.",
        },
        "env": {
            "UNPAYWALL_EMAIL": "beravendanho@gmail.com",
            "PAPER_FETCH_INSTITUTIONAL": "1",
            "PAPER_FETCH_NO_SCIHUB": "Optional. Set to any value to disable the Sci-Hub fallback (enabled by default).",
            "PAPER_FETCH_SCIHUB_MIRRORS": "Optional. Comma-separated list of Sci-Hub mirror hostnames to try, in priority order, overriding the built-in defaults (e.g. 'sci-hub.ru,sci-hub.st,sci-hub.su').",
            "PAPER_FETCH_NO_LIBGEN": "Optional. Set to any value to disable the Library Genesis fallback (enabled by default).",
            "PAPER_FETCH_LIBGEN_MIRRORS": "Optional. Comma-separated list of Libgen mirror URLs to try, in priority order (e.g. 'https://libgen.li,https://libgen.vg,https://libgen.la').",
            "PAPER_FETCH_CLOAK": "Optional. Set to any value to enable the CloakBrowser fallback: when a download is blocked by Cloudflare (HTTP 403/429 or a non-PDF interstitial), the URL is retried through a stealth Chromium that can pass the JS challenge. Off by default; requires the cloak_pdf.py companion and a cloakbrowser-importable Python (see CLOAKBROWSER_PYTHON). Bytes are re-validated through the same %PDF + 50 MB checks. Operator action only — the agent cannot opt in.",
            "CLOAKBROWSER_PYTHON": "Optional. Path to a Python interpreter that can import cloakbrowser, used by the PAPER_FETCH_CLOAK fallback. If unset, the current interpreter is checked.",
            "PAPER_FETCH_CLOAK_HEADED": "Optional. Set to any value to make the cloak fallback launch a headed (visible) browser instead of headless. Harder Cloudflare challenges (e.g. science.org) defeat headless mode; the headed window clears them. Requires a display. Read by the cloak_pdf.py companion.",
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

EPILOG = """\
exit codes:
  0  all DOIs resolved successfully
  1  unresolved (some DOIs had no OA copy; no transport failure)
  3  validation error (bad arguments)
  4  transport error (network / download / IO failure; retryable class)

subcommands:
  schema                 print the machine-readable CLI schema and exit (no network)

stdin:
  paper-fetch -          read a single DOI from stdin
  paper-fetch --batch -  read DOIs line-by-line from stdin

output:
  stdout emits one JSON object per invocation (NDJSON with --stream).
  stderr emits NDJSON progress events when --format json, prose when --format text.
  stdout format auto-detects TTY: json when piped/captured, text in a terminal.

examples:
  %(prog)s 10.1038/s41586-020-2649-2
  %(prog)s 10.1038/s41586-020-2649-2 --dry-run
  %(prog)s --batch dois.txt --out ./papers --format text
  echo 10.1038/s41586-020-2649-2 | %(prog)s --batch -
  %(prog)s schema
"""


def _load_dois_from_args(args) -> list[str] | dict:
    """Parse DOI input from args. Returns list of DOIs or an error envelope dict.

    Title resolution (``--title``) is handled separately by ``_resolve_title``
    in main(); this loader sees only the resolved DOI by then.
    """
    inputs = [bool(args.batch), bool(args.doi), bool(getattr(args, "title", None))]
    if sum(inputs) > 1:
        return _envelope_err(
            "validation_error",
            "Pass exactly one of: positional DOI, --batch FILE, or --title TITLE.",
        )
    if args.batch:
        if args.batch == "-":
            text = sys.stdin.read()
            dois = [l.strip() for l in text.splitlines() if l.strip()]
        else:
            batch_path = Path(args.batch)
            if not batch_path.exists():
                return _envelope_err(
                    "validation_error",
                    f"Batch file not found: {args.batch}",
                    field="batch",
                )
            dois = [l.strip() for l in batch_path.read_text().splitlines() if l.strip()]
    elif args.doi == "-":
        text = sys.stdin.read()
        dois = [l.strip() for l in text.splitlines() if l.strip()]
    elif args.doi:
        # Check if the argument is an existing file (batch mode fallback)
        doi_as_file = Path(args.doi)
        if doi_as_file.is_file():
            dois = [l.strip() for l in doi_as_file.read_text().splitlines() if l.strip()]
        else:
            dois = [args.doi]
    else:
        return _envelope_err("validation_error", "Provide a DOI, --title, or --batch file")

    if not dois:
        return _envelope_err("validation_error", "No DOIs found in input")
    return dois




def _resolve_title(title: str, *, timeout: int) -> tuple[str | None, dict]:
    """Resolve a title to a DOI via Crossref → Semantic Scholar → OpenAlex → Europe PMC fallback chain.

    Always populates a ``resolution_meta`` dict (at least ``query`` and
    ``resolvers_tried``) so callers can surface it in the envelope's meta slot.
    """
    clean_title = re.sub(r"^[\[\"'‘“\s]+|[\]\"'’”\.\s]+$", "", title.strip())
    _progress("title_resolve_try", query=clean_title or title)
    resolvers_tried: list[str] = []

    # Pass 1 — Crossref. Confident hit short-circuits the chain.
    resolvers_tried.append("crossref")
    cr_doi, cr_top, cr_candidates = try_crossref_title(clean_title or title, timeout=timeout)
    cr_score = cr_top.get("score") if cr_top else None
    cr_gap: float | None = None
    if len(cr_candidates) >= 2:
        s0 = cr_candidates[0].get("score")
        s1 = cr_candidates[1].get("score")
        if isinstance(s0, (int, float)) and isinstance(s1, (int, float)):
            cr_gap = float(s0) - float(s1)
    cr_low_reason = _classify_low_confidence(cr_score, cr_gap) if cr_doi else "no_match"

    if cr_doi and cr_low_reason is None:
        _progress(
            "title_resolve_hit",
            query=title,
            resolver="crossref",
            doi=cr_doi,
            title=cr_top.get("title"),
            score=cr_score,
        )
        return cr_doi, {
            "query": title,
            "resolver": "crossref",
            "resolvers_tried": resolvers_tried,
            "resolved_doi": cr_doi,
            "resolved_title": cr_top.get("title"),
            "match_score": cr_score,
            "candidates": cr_candidates,
            "low_confidence": False,
        }

    # Pass 2 — Semantic Scholar match endpoint. Covers arXiv-only papers
    # (no Crossref DOI) and rescues low-confidence Crossref matches.
    _progress(
        "title_resolver_try",
        query=title,
        resolver="semantic_scholar",
        reason="crossref_" + cr_low_reason if cr_low_reason else "crossref_no_match",
    )
    resolvers_tried.append("semantic_scholar")
    s2_doi, s2_meta = try_semantic_scholar_match(clean_title or title, timeout=timeout)
    if s2_doi:
        _progress(
            "title_resolve_hit",
            query=title,
            resolver="semantic_scholar",
            doi=s2_doi,
            title=s2_meta.get("title"),
        )
        out: dict = {
            "query": title,
            "resolver": "semantic_scholar",
            "resolvers_tried": resolvers_tried,
            "resolved_doi": s2_doi,
            "resolved_title": s2_meta.get("title"),
            "candidates": [s2_meta],
            "low_confidence": False,
            "fallback_reason": cr_low_reason,
        }
        # Preserve the Crossref candidate list so an agent can compare what
        # each resolver thought was the top hit (helps when the two disagree).
        if cr_candidates:
            out["crossref_candidates"] = cr_candidates
        return s2_doi, out

    # Pass 3 — OpenAlex Title Search fallback
    try:
        oa_query = urllib.parse.quote(clean_title or title)
        oa_mailto = os.environ.get("OPENALEX_MAILTO") or EMAIL
        oa_url = f"https://api.openalex.org/works?search={oa_query}&per-page=3"
        if oa_mailto:
            oa_url += f"&mailto={urllib.parse.quote(oa_mailto)}"
        oa_data = _get_json(oa_url, timeout=timeout)
        results = (oa_data or {}).get("results") or []
        for res_item in results:
            raw_doi = (res_item.get("doi") or "").replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
            if raw_doi and _DOI_RE.match(raw_doi):
                resolvers_tried.append("openalex")
                _progress(
                    "title_resolve_hit",
                    query=title,
                    resolver="openalex",
                    doi=raw_doi,
                    title=res_item.get("title"),
                )
                return raw_doi, {
                    "query": title,
                    "resolver": "openalex",
                    "resolvers_tried": resolvers_tried,
                    "resolved_doi": raw_doi,
                    "resolved_title": res_item.get("title"),
                    "candidates": results,
                    "low_confidence": False,
                }
    except Exception:
        pass

    # Pass 4 — Europe PMC Title Search fallback
    try:
        epmc_query = urllib.parse.quote(clean_title or title)
        epmc_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={epmc_query}&format=json&pageSize=3"
        epmc_data = _get_json(epmc_url, timeout=timeout)
        epmc_results = (epmc_data or {}).get("resultList", {}).get("result") or []
        for r_item in epmc_results:
            r_doi = (r_item.get("doi") or "").strip()
            if r_doi and _DOI_RE.match(r_doi):
                resolvers_tried.append("europe_pmc")
                _progress(
                    "title_resolve_hit",
                    query=title,
                    resolver="europe_pmc",
                    doi=r_doi,
                    title=r_item.get("title"),
                )
                return r_doi, {
                    "query": title,
                    "resolver": "europe_pmc",
                    "resolvers_tried": resolvers_tried,
                    "resolved_doi": r_doi,
                    "resolved_title": r_item.get("title"),
                    "candidates": epmc_results,
                    "low_confidence": False,
                }
    except Exception:
        pass

    # Pass 5 — every resolver missed. If Crossref had *any* candidate, return
    # it with a low_confidence flag so the agent can either (a) proceed with
    # caution or (b) bail out via the dry-run preview.
    if cr_doi:
        _progress(
            "title_resolve_hit",
            query=title,
            resolver="crossref",
            doi=cr_doi,
            title=cr_top.get("title"),
            score=cr_score,
            low_confidence=True,
            reason=cr_low_reason,
        )
        return cr_doi, {
            "query": title,
            "resolver": "crossref",
            "resolvers_tried": resolvers_tried,
            "resolved_doi": cr_doi,
            "resolved_title": cr_top.get("title"),
            "match_score": cr_score,
            "candidates": cr_candidates,
            "low_confidence": True,
            "low_confidence_reason": cr_low_reason,
        }

    _progress("title_resolve_miss", query=title, resolvers_tried=resolvers_tried)
    return None, {
        "query": title,
        "resolvers_tried": resolvers_tried,
        "candidates": [],
    }


# ===========================================================================
# EXPANDED LEGITIMATE DISCOVERY LAYER (ADDED — ORIGINAL CODE PRESERVED)
# ===========================================================================
# This layer is intentionally additive: the original fetch implementation,
# original sources, and original CLI remain intact above. The new wrapper below
# adds additional metadata/repository discovery only after the original fetch
# path has been exhausted, while title resolution is replaced by a stricter
# multi-database resolver.
#
# Added legitimate/open research sources:
#   - OpenAIRE Graph
#   - HAL Open Archive
#   - Zenodo Records API
#   - DataCite REST API
#   - DOAJ article search (best-effort metadata/PDF links)
#
# Existing sources are preserved and remain usable:
#   Unpaywall, Semantic Scholar, OpenAlex, Europe PMC, PMC, PubMed,
#   arXiv, bioRxiv/medRxiv, CORE, Crossref links, publisher-direct,
#   ACL Anthology, DOI resolver, and the pre-existing fallbacks.
#
# OpenAIRE currently exposes a production Graph v3 endpoint and documents
# DOI/title-based research-product search. DataCite exposes public DOI metadata
# retrieval/search. HAL exposes a public search API, and Zenodo exposes a
# records search API. These are used here only for discovery of publicly
# available records/files; a candidate is accepted only after PDF validation.

from difflib import SequenceMatcher
import unicodedata


EXPANDED_SOURCE_NAMES = {
    "openaire": "OpenAIRE",
    "hal": "HAL",
    "zenodo": "Zenodo",
    "datacite": "DataCite",
    "doaj": "DOAJ",
    "dryad": "Dryad",
    "figshare": "Figshare",
    "ntrs": "NASA NTRS",
    "base": "BASE",
    "fatcat": "Internet Archive Scholar",
}

# BASE answers only registered IPs; once it refuses, stop asking this run.
_base_blocked = False

try:
    SOURCE_NAMES.update(EXPANDED_SOURCE_NAMES)  # type: ignore[name-defined]
except Exception:
    pass


































_fatcat_failures = 0
FATCAT_MAX_FAILURES = 2




def _candidate_records_for_title(title: str, *, timeout: int) -> list[dict]:
    """Gather title candidates from all available metadata indexes concurrently."""
    candidates: list[dict] = []

    def fetch_cr():
        cr_doi, cr_top, cr_candidates = try_crossref_title(title, timeout=timeout)
        return [{"resolver": "crossref", "doi": normalize_doi(str(item.get("doi") or "")), "title": item.get("title"), "year": item.get("year"), "author": item.get("author"), "journal": item.get("journal"), "raw_score": item.get("score")} for item in cr_candidates]

    def fetch_s2():
        s2_doi, s2_meta = try_semantic_scholar_match(title, timeout=timeout)
        if s2_doi:
            return [{"resolver": "semantic_scholar", "doi": normalize_doi(s2_doi), "title": s2_meta.get("title"), "year": s2_meta.get("year"), "author": s2_meta.get("author"), "journal": s2_meta.get("journal")}]
        return []

    def fetch_oa():
        params = {"search": title, "per-page": 5, "select": "id,doi,title,publication_year,authorships,primary_location,open_access"}
        oa_mailto = os.environ.get("OPENALEX_MAILTO") or EMAIL
        if oa_mailto: params["mailto"] = oa_mailto
        data = _get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params), timeout=timeout)
        res = []
        for item in (data or {}).get("results") or []:
            raw_doi = item.get("doi") or ""
            raw_doi = raw_doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
            authorships = item.get("authorships") or []
            author = ((authorships[0].get("author") or {}).get("display_name")) if authorships else None
            res.append({"resolver": "openalex", "doi": normalize_doi(raw_doi), "title": item.get("title"), "year": item.get("publication_year"), "author": author, "journal": ((item.get("primary_location") or {}).get("source") or {}).get("display_name")})
        return res

    def fetch_epmc():
        params = {"query": f'TITLE:"{title}"', "format": "json", "pageSize": 5}
        data = _get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params), timeout=timeout)
        return [{"resolver": "europe_pmc", "doi": normalize_doi(str(item.get("doi") or "")), "title": item.get("title"), "year": item.get("pubYear"), "author": item.get("authorString"), "journal": item.get("journalTitle")} for item in (data or {}).get("resultList", {}).get("result") or []]

    def fetch_ntrs():
        return _try_ntrs_records(title, timeout=timeout)
        
    def fetch_pubmed():
        return _try_pubmed_title_records(title, timeout=timeout)

    tasks = [
        fetch_cr, fetch_s2, fetch_oa, fetch_epmc, fetch_ntrs, fetch_pubmed,
        lambda: [{"resolver": "openaire", "doi": normalize_doi(str(m.get("doi") or m.get("datacite_doi") or "")), "title": m.get("title"), "year": _extract_first_year(m), "author": m.get("author")} for _, m, _ in [try_openaire(title=title, timeout=timeout)] if m],
        lambda: [{"resolver": "hal", "doi": normalize_doi(str(m.get("doi") or m.get("datacite_doi") or "")), "title": m.get("title"), "year": _extract_first_year(m), "author": m.get("author")} for _, m, _ in [try_hal(title=title, timeout=timeout)] if m],
        lambda: [{"resolver": "datacite", "doi": normalize_doi(str(m.get("doi") or m.get("datacite_doi") or "")), "title": m.get("title"), "year": _extract_first_year(m), "author": m.get("author")} for _, m, _ in [try_datacite(title=title, timeout=timeout)] if m],
        lambda: [{"resolver": "zenodo", "doi": normalize_doi(str(m.get("doi") or m.get("datacite_doi") or "")), "title": m.get("title"), "year": _extract_first_year(m), "author": m.get("author")} for _, m, _ in [try_zenodo(title=title, timeout=timeout)] if m],
        lambda: [{"resolver": "doaj", "doi": normalize_doi(str(m.get("doi") or m.get("datacite_doi") or "")), "title": m.get("title"), "year": _extract_first_year(m), "author": m.get("author")} for _, m, _ in [try_doaj(title=title, timeout=timeout)] if m],
    ]

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = [executor.submit(_inherit_budget(t)) for t in tasks]
        for fut in concurrent.futures.as_completed(futures):
            try:
                res = fut.result()
                if res:
                    candidates.extend(res)
            except Exception:
                pass

    return candidates



def _resolve_title_expanded(title: str, *, timeout: int) -> tuple[str | None, dict]:
    """Multi-index title resolver with strict similarity validation."""
    clean_title = re.sub(r"^[\[\"'‘“\s]+|[\]\"'’”\.\s]+$", "", title.strip())
    clean_title = clean_title or title.strip()
    _progress("title_resolve_try", query=clean_title, resolver="expanded_multi_source")
    candidates = _candidate_records_for_title(clean_title, timeout=timeout)
    best, ranked = _rank_title_candidates(clean_title, candidates)

    resolution = {
        "query": title,
        "resolver": "expanded_multi_source",
        "resolvers_tried": sorted({c.get("resolver") for c in candidates if c.get("resolver")}),
        "candidates": ranked,
        "low_confidence": True,
    }

    if not best:
        resolution["low_confidence_reason"] = "no_match"
        _progress("title_resolve_miss", query=title, resolver="expanded_multi_source")
        return None, resolution

    best_score = float(best.get("rank_score") or 0.0)
    second_score = float(ranked[1].get("rank_score") or 0.0) if len(ranked) > 1 else 0.0
    gap = best_score - second_score
    resolution.update({
        "resolved_doi": best.get("doi"),
        "resolved_title": best.get("title"),
        "match_score": best_score,
        "title_similarity": best.get("title_similarity"),
        "candidate_gap": round(gap, 6),
        "resolver_selected": best.get("resolver"),
    })

    # Strict default for title-only resolution. Do not reproduce the old
    # behaviour of returning a weak Crossref hit simply because a result exists.
    if not best.get("doi"):
        resolution["low_confidence_reason"] = "no_doi"
        return None, resolution

    if best_score < 0.90:
        resolution["low_confidence_reason"] = "title_similarity_below_threshold"
        return None, resolution

    if len(ranked) > 1 and gap < 0.03 and best_score < 0.98:
        resolution["low_confidence_reason"] = "ambiguous_top_candidates"
        return None, resolution

    resolution["low_confidence"] = False
    _progress(
        "title_resolve_hit",
        query=title,
        resolver=best.get("resolver"),
        doi=best.get("doi"),
        title=best.get("title"),
        score=best_score,
    )
    return normalize_doi(str(best["doi"])), resolution


# Preserve the original resolver for compatibility/debugging while routing new
# title searches through the strict multi-source resolver.
_resolve_title_original = _resolve_title
_resolve_title = _resolve_title_expanded


# Keep a handle to the original DOI fetch implementation. The wrapper below
# only adds new discovery sources after the original source chain returns a
# non-success result.
_fetch_original = fetch


def _expanded_download_candidate(
    doi: str,
    out_dir: Path,
    source: str,
    url: str,
    *,
    timeout: int,
    overwrite: bool,
    candidates: list[dict],
    meta: dict,
    record_doi_matched: bool = False,
) -> dict | None:
    """Download+validate a candidate from the expanded (OpenAIRE/HAL/Zenodo/
    DataCite/DOAJ) discovery layer.

    ``record_doi_matched`` defaults to False here (unlike the primary
    fetch() loop's default of True): even after the try_* resolvers were
    fixed to only surface hits whose own DOI/title genuinely matched, these
    remain search-index sources rather than doi-exact-keyed lookups, so the
    identity gate always re-derives evidence from the downloaded PDF itself
    rather than skipping straight to a record-level pass.
    """
    if not url:
        return None
    candidate = {"source": source, "url": url}
    if candidate not in candidates:
        candidates.append(candidate)

    expected = _expected_identity(doi, meta)

    if not overwrite:
        existing = _find_cached_pdf_for_doi(out_dir, doi)
        if existing is not None:
            cached = _read_identity_sidecar(existing)
            if cached is None:
                cached = _validate_downloaded_file(existing, expected=expected, record_doi_matched=record_doi_matched)
                if cached["identity_validated"]:
                    _write_identity_sidecar(existing, cached)
            if cached["identity_validated"]:
                return {
                    "doi": doi,
                    "success": True,
                    "source": "cache",
                    "pdf_url": url,
                    "file": str(existing),
                    "meta": meta or {},
                    "sources_tried": [],
                    "skipped": True,
                    **_identity_result_fields(cached),
                }
            _quarantine_unvalidated_file(existing)

    fname = _filename(meta or {"title": doi})
    dest = out_dir / fname
    err = _download(url, dest, timeout=timeout)
    if err is None:
        verdict = _validate_downloaded_file(dest, expected=expected, record_doi_matched=record_doi_matched)
        _progress(
            "validation_result", doi=doi, source=source, url=url,
            result="CONFIRMED" if verdict["identity_validated"] else "REJECTED",
            method=verdict["validation_method"], expected_doi=doi, detected_doi=verdict.get("detected_doi"),
            expected_title=expected.get("title"), detected_title=verdict.get("detected_title"),
            phase="expanded",
        )
        if not verdict["identity_validated"]:
            try:
                dest.unlink()
            except OSError:
                pass
            return None
        _write_identity_sidecar(dest, verdict)
        return {
            "doi": doi,
            "success": True,
            "source": source,
            "pdf_url": url,
            "file": str(dest),
            "meta": meta or {},
            "sources_tried": [source],
            "candidates": candidates,
            "expanded_discovery": True,
            **_identity_result_fields(verdict),
        }
    try:
        if dest.exists() and not validate_pdf_data(dest.read_bytes())[0]:
            dest.unlink()
    except Exception:
        pass
    return None


def _fetch_from_expanded_sources(
    doi: str,
    out_dir: Path,
    *,
    dry_run: bool,
    overwrite: bool,
    timeout: int,
    original_result: dict,
) -> dict:
    """Search additional legitimate metadata/repository sources concurrently."""
    if dry_run:
        original_result.setdefault("expanded_discovery", {})
        original_result["expanded_discovery"]["status"] = "skipped_for_dry_run"
        return original_result

    sources_tried: list[str] = list(original_result.get("sources_tried") or [])
    candidates: list[dict] = []
    meta = dict(original_result.get("meta") or {})
    errors: list[dict] = []
    
    resolvers = [
        ("openaire", lambda: try_openaire(doi=doi, timeout=timeout)),
        ("hal", lambda: try_hal(doi=doi, timeout=timeout)),
        ("zenodo", lambda: try_zenodo(doi=doi, timeout=timeout)),
        ("datacite", lambda: try_datacite(doi=doi, timeout=timeout)),
        ("doaj", lambda: try_doaj(doi=doi, timeout=timeout)),
        ("dryad", lambda: try_dryad(doi=doi, timeout=timeout)),
        ("figshare", lambda: try_figshare(doi=doi, timeout=timeout)),
        ("ntrs", lambda: try_ntrs(doi=doi, timeout=timeout)),
        ("base", lambda: try_base_search(doi=doi, timeout=timeout)),
        ("fatcat", lambda: try_fatcat(doi=doi, timeout=timeout)),
    ]
    
    import concurrent.futures
    
    def run_resolver(name, resolver_func):
        try:
            urls, extra_meta, raw = resolver_func()
            return name, urls, extra_meta, raw, None
        except Exception as exc:
            return name, None, None, None, str(exc)
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(resolvers)) as executor:
        futures = [executor.submit(_inherit_budget(run_resolver), name, func) for name, func in resolvers]
        for fut in concurrent.futures.as_completed(futures):
            name, urls, extra_meta, raw, exc_str = fut.result()
            if name not in sources_tried:
                sources_tried.append(name)
            
            if exc_str:
                errors.append({"source": name, "reason": exc_str})
                _progress("source_miss", doi=doi, source=name, reason=exc_str, phase="expanded")
                continue
                
            for key, value in (extra_meta or {}).items():
                if value and not meta.get(key):
                    meta[key] = value
                    
            if urls:
                for url in urls:
                    if not url:
                        continue
                    _progress("source_hit", doi=doi, source=name, pdf_url=url, phase="expanded")
                    result = _expanded_download_candidate(
                        doi,
                        out_dir,
                        name,
                        url,
                        timeout=timeout,
                        overwrite=overwrite,
                        candidates=candidates,
                        meta=meta,
                    )
                    if result:
                        result["meta"] = meta
                        result["sources_tried"] = sources_tried
                        result.setdefault("title_resolution", original_result.get("title_resolution"))
                        return result
                    
            _progress("source_miss", doi=doi, source=name, phase="expanded")

    title = meta.get("title")
    if title:
        title_resolvers = [
            ("openaire", lambda: try_openaire(title=title, timeout=timeout)),
            ("hal", lambda: try_hal(title=title, timeout=timeout)),
            ("zenodo", lambda: try_zenodo(title=title, timeout=timeout)),
            ("datacite", lambda: try_datacite(title=title, timeout=timeout)),
            ("doaj", lambda: try_doaj(title=title, timeout=timeout)),
            ("dryad", lambda: try_dryad(title=title, timeout=timeout)),
            ("figshare", lambda: try_figshare(title=title, timeout=timeout)),
            ("ntrs", lambda: try_ntrs(title=title, timeout=timeout)),
            ("base", lambda: try_base_search(title=title, timeout=timeout)),
            ("fatcat", lambda: try_fatcat(title=title, timeout=timeout)),
        ]
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(title_resolvers)) as executor:
            futures = [executor.submit(_inherit_budget(run_resolver), name, func) for name, func in title_resolvers]
            for fut in concurrent.futures.as_completed(futures):
                name, urls, extra_meta, raw, exc_str = fut.result()
                if name not in sources_tried:
                    sources_tried.append(name)
                    
                if exc_str:
                    errors.append({"source": name, "reason": exc_str})
                    _progress("source_miss", doi=doi, source=name, reason=exc_str, phase="expanded")
                    continue
                    
                for key, value in (extra_meta or {}).items():
                    if value and not meta.get(key):
                        meta[key] = value
                        
                if urls:
                    for url in urls:
                        if not url:
                            continue
                        _progress("source_hit", doi=doi, source=name, pdf_url=url, phase="expanded")
                        result = _expanded_download_candidate(
                            doi,
                            out_dir,
                            name,
                            url,
                            timeout=timeout,
                            overwrite=overwrite,
                            candidates=candidates,
                            meta=meta,
                        )
                        if result:
                            result["meta"] = meta
                            result["sources_tried"] = sources_tried
                            result.setdefault("title_resolution", original_result.get("title_resolution"))
                            return result
                        
                _progress("source_miss", doi=doi, source=name, phase="expanded")
                
    original_result["sources_tried"] = sources_tried
    original_result["expanded_discovery"] = {
        "status": "exhausted",
        "sources": sources_tried,
        "candidates": candidates,
        "errors": errors,
        "metadata": meta,
    }
    original_result["meta"] = meta
    return original_result


# ===========================================================================
# END OF ADDITIVE EXPANDED DISCOVERY LAYER

def fetch(
    doi: str,
    out_dir: Path,
    *,
    dry_run: bool,
    overwrite: bool,
    timeout: int,
    sources: list[str] | None = None,
) -> dict:
    """Additive wrapper around the original fetch() with expanded discovery."""
    result = _fetch_original(
        doi,
        out_dir,
        dry_run=dry_run,
        overwrite=overwrite,
        timeout=timeout,
        sources=sources,
    )
    if result.get("success") or dry_run or _deadline_exceeded():
        return result

    # Respect explicit source filtering. If the caller supplied --sources and
    # none of the new sources were requested, do not surprise them with extra calls.
    expanded_allowed = {s.strip().lower() for s in sources} if sources else None
    if expanded_allowed is not None and not expanded_allowed.intersection(EXPANDED_SOURCE_NAMES):
        return result

    return _fetch_from_expanded_sources(
        normalize_doi(doi),
        out_dir,
        dry_run=dry_run,
        overwrite=overwrite,
        timeout=timeout,
        original_result=result,
    )


# ===========================================================================
# END OF ADDITIVE EXPANDED DISCOVERY LAYER



















def main():
    global _format, _pretty, _stream, _request_id, _started_monotonic

    _started_monotonic = time.monotonic()
    _request_id = f"req_{uuid.uuid4().hex[:12]}"

    # Schema subcommand - handle before the main parser so we don't require a DOI.
    if len(sys.argv) >= 2 and sys.argv[1] == "schema":
        # Honor --pretty / --format if they follow.
        rest = sys.argv[2:]
        _pretty = "--pretty" in rest
        if "--format" in rest:
            i = rest.index("--format")
            if i + 1 < len(rest) and rest[i + 1] in ("json", "text"):
                _format = rest[i + 1]
            else:
                _format = _default_format()
        else:
            _format = _default_format()
        schema = build_schema()
        _emit(_envelope_ok(schema))
        sys.exit(EXIT_SUCCESS)

    ap = argparse.ArgumentParser(
        prog="paper-fetch",
        description="Fetch legal open-access PDFs by DOI via Unpaywall, Semantic Scholar, arXiv, Europe PMC, PMC, PubMed, and bioRxiv/medRxiv.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("doi", nargs="?", help="DOI to fetch (e.g. 10.1038/s41586-020-2649-2). Use '-' to read from stdin.")
    ap.add_argument("--title", metavar="TITLE", help="paper title; resolved to a DOI via Crossref before download. Mutually exclusive with positional DOI / --batch.")
    ap.add_argument("--batch", metavar="FILE", help="input file. Supports DOI-only files or mixed files: DOIs first, then article titles. Use '-' for DOI-only stdin.")
    ap.add_argument("--out", default="pdfs", metavar="DIR", help="output directory (default: pdfs)")
    ap.add_argument("--dry-run", action="store_true", help="resolve sources without downloading; preview the PDF URL and filename")
    ap.add_argument(
        "--format",
        choices=["json", "text"],
        default=None,
        dest="fmt",
        help="output format. json for agents, text for humans. Default: json when stdout is not a TTY, text otherwise.",
    )
    ap.add_argument("--pretty", action="store_true", help="pretty-print JSON output (2-space indent)")
    ap.add_argument("--stream", action="store_true", help="emit one NDJSON result per line on stdout as each DOI resolves (batch mode)")
    ap.add_argument("--overwrite", action="store_true", help="re-download even if the destination file already exists")
    ap.add_argument("--sources", metavar="SOURCES", default=None, help="comma-separated list of sources to query (e.g. 'unpaywall,openalex' or 'libgen')")
    ap.add_argument("--idempotency-key", metavar="KEY", default=None, help="safe-retry key; re-running with the same key replays the original envelope from <out>/.paper-fetch-idem/")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, metavar="SECONDS", help=f"HTTP timeout in seconds per request (default: {DEFAULT_TIMEOUT})")
    ap.add_argument("--version", action="version", version=f"paper-fetch {CLI_VERSION} (schema {SCHEMA_VERSION})")
    args = ap.parse_args()

    _format = args.fmt or _default_format()
    _pretty = args.pretty
    _stream = args.stream

    # Title-aware input mode:
    #   * no arguments -> DOI's.txt
    #   * --batch FILE -> same mixed DOI/title parser when trailing titles exist
    special_input: Path | None = None
    if len(sys.argv) == 1:
        default_doi_file = Path("DOI's.txt")
        if default_doi_file.is_file():
            special_input = default_doi_file
    elif args.batch and args.batch != "-":
        candidate = Path(args.batch)
        if candidate.is_file():
            _dois_probe, _titles_probe = _load_dois_and_titles_from_file(candidate)
            if _titles_probe:
                special_input = candidate

    if special_input is not None:
            # -----------------------------------------------------------
            # 1. Read DOI's.txt and separate DOIs from trailing titles.
            # -----------------------------------------------------------
            dois, article_titles = _load_dois_and_titles_from_file(special_input)

            if not dois:
                _emit(_envelope_err(
                    "validation_error",
                    "No DOIs found in default file DOI's.txt",
                ))
                sys.exit(EXIT_VALIDATION)

            print(
                f"{len(dois)} DOIs encontrados",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"{len(article_titles)} títulos encontrados no final do arquivo",
                file=sys.stderr,
                flush=True,
            )

            out_dir = Path(args.out)
            out_dir.mkdir(parents=True, exist_ok=True)

            # Special mode is retained for the same behavior used by the
            # original automatic DOI processing path.
            os.environ["PAPER_FETCH_SPECIAL_MODE"] = "1"
            timeout_val = args.timeout

            # -----------------------------------------------------------
            # 2. Process EVERY DOI first.
            # -----------------------------------------------------------
            results: list[dict] = []

            print(
                "\n========== PROCESSANDO DOIs ==========",
                file=sys.stderr,
                flush=True,
            )

            for index, doi in enumerate(dois, start=1):
                print(
                    f"\n[DOI {index}/{len(dois)}] {doi}",
                    file=sys.stderr,
                    flush=True,
                )

                try:
                    result = fetch(
                        doi,
                        out_dir,
                        dry_run=False,
                        overwrite=args.overwrite,
                        timeout=timeout_val,
                    )
                except Exception as exc:
                    result = {
                        "doi": doi,
                        "success": False,
                        "error": {
                            "code": "internal_error",
                            "message": str(exc),
                        },
                    }
                    print(
                        f"ERRO processando DOI {doi}: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )

                results.append(result)

            # -----------------------------------------------------------
            # 3. ONLY AFTER ALL DOIs ARE FINISHED, search the titles.
            # -----------------------------------------------------------
            title_results: list[dict] = []

            if article_titles:
                print(
                    "\n========== PROCESSANDO ARTIGOS PELO NOME ==========",
                    file=sys.stderr,
                    flush=True,
                )

                for index, title in enumerate(article_titles, start=1):
                    print(
                        f"\n[ARTIGO {index}/{len(article_titles)}] {title}",
                        file=sys.stderr,
                        flush=True,
                    )

                    try:
                        # Resolve title -> DOI using the existing Crossref /
                        # Semantic Scholar resolution chain.
                        print(
                            f"Pesquisando título: {title}",
                            file=sys.stderr,
                            flush=True,
                        )

                        resolved_doi, title_resolution = _resolve_title(
                            title,
                            timeout=timeout_val,
                        )

                        if not resolved_doi:
                            result = {
                                "title": title,
                                "searched_title": title,
                                "success": False,
                                "resolved_doi": None,
                                "title_resolution": title_resolution,
                                "search_method": "title",
                                "error": {
                                    "code": "title_resolve_failed",
                                    "message": (
                                        f"Nenhum DOI encontrado para o título: {title}"
                                    ),
                                },
                            }
                            title_results.append(result)

                            print(
                                f"Não foi possível encontrar DOI para: {title}",
                                file=sys.stderr,
                                flush=True,
                            )
                            continue

                        print(
                            f"DOI encontrado: {resolved_doi}",
                            file=sys.stderr,
                            flush=True,
                        )

                        # Use the exact same download pipeline as a normal DOI.
                        result = fetch(
                            resolved_doi,
                            out_dir,
                            dry_run=False,
                            overwrite=False,
                            timeout=timeout_val,
                        )

                        result["searched_title"] = title
                        result["title_resolution"] = title_resolution
                        result["resolved_doi"] = resolved_doi
                        result["search_method"] = "title"
                        title_results.append(result)

                        if result.get("success"):
                            print(
                                f"DOWNLOAD OK: {title}",
                                file=sys.stderr,
                                flush=True,
                            )
                        else:
                            print(
                                f"PDF não encontrado para: {title}",
                                file=sys.stderr,
                                flush=True,
                            )

                    except Exception as exc:
                        result = {
                            "title": title,
                            "searched_title": title,
                            "success": False,
                            "search_method": "title",
                            "error": {
                                "code": "internal_error",
                                "message": str(exc),
                            },
                        }
                        title_results.append(result)

                        print(
                            f"ERRO pesquisando título '{title}': {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
            else:
                print(
                    "\nNenhum título foi encontrado após o último DOI.",
                    file=sys.stderr,
                    flush=True,
                )

            # -----------------------------------------------------------
            # 4. Keep the original DOI report.
            # -----------------------------------------------------------
            report = generate_detailed_report(results, dois)
            Path("Relatório.txt").write_text(report, encoding="utf-8")

            # -----------------------------------------------------------
            # 5. Write a separate title-search report.
            # -----------------------------------------------------------
            _write_title_report(
                title_results,
                article_titles,
                Path("Relatório_artigos_por_nome.txt"),
            )

            # -----------------------------------------------------------
            # 6. Final summary.
            # -----------------------------------------------------------
            doi_successes = sum(1 for r in results if r.get("success"))
            title_successes = sum(1 for r in title_results if r.get("success"))
            title_failures = len(title_results) - title_successes

            print(
                "\n==========================================",
                file=sys.stderr,
                flush=True,
            )
            print(
                "PROCESSAMENTO FINALIZADO",
                file=sys.stderr,
                flush=True,
            )
            print(
                "==========================================",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"DOIs processados: {len(dois)}",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"DOIs baixados com sucesso: {doi_successes}",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"Artigos pesquisados por nome: {len(article_titles)}",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"Artigos baixados com sucesso: {title_successes}",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"Artigos não encontrados/baixados: {title_failures}",
                file=sys.stderr,
                flush=True,
            )
            print(
                "Relatório dos DOIs: Relatório.txt",
                file=sys.stderr,
                flush=True,
            )
            print(
                "Relatório dos artigos por nome: Relatório_artigos_por_nome.txt",
                file=sys.stderr,
                flush=True,
            )

            os.environ.pop("PAPER_FETCH_NO_SCIHUB", None)
            os.environ.pop("PAPER_FETCH_SPECIAL_MODE", None)

            # Combine both result sets for the same exit-code behavior.
            sys.exit(_decide_exit(results + title_results))

    # One-time session header — lets agents detect schema drift on the very
    # first stderr line, before any per-DOI work or network I/O.
    _progress("session", cli_version=CLI_VERSION, schema_version=SCHEMA_VERSION)

    if not EMAIL:
        _progress("source_skip", source="unpaywall", reason="UNPAYWALL_EMAIL not set (top-level notice)")

    out_dir = Path(args.out)

    # Title resolution — runs before DOI loading so the rest of the pipeline
    # treats the resolved DOI as if it had been passed directly.
    title_resolution: dict | None = None
    if args.title:
        # Reject simultaneous title + DOI / --batch up front rather than later.
        if args.doi or args.batch:
            _emit(_envelope_err(
                "validation_error",
                "--title cannot be combined with a positional DOI or --batch.",
            ))
            sys.exit(EXIT_VALIDATION)
        resolved_doi, title_resolution = _resolve_title(args.title, timeout=args.timeout)
        if not resolved_doi:
            direct = fetch_title_direct(
                args.title,
                out_dir,
                timeout=args.timeout,
                overwrite=args.overwrite,
                sources=[s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None,
            )
            direct["title_resolution"] = {
                **(title_resolution or {}),
                "direct_recovery": direct.get("title_resolution"),
            }
            _emit(_envelope_ok(
                {"results": [direct], "summary": {
                    "total": 1,
                    "succeeded": 1 if direct.get("success") else 0,
                    "failed": 0 if direct.get("success") else 1,
                }},
                ok=True if direct.get("success") else "partial",
            ))
            sys.exit(EXIT_SUCCESS if direct.get("success") else EXIT_UNRESOLVED)
        # Inject the resolved DOI as the positional argument so downstream
        # logic (DOI validation, fetch loop, idempotency replay) is identical.
        # Clear args.title so the mutual-exclusion guard in _load_dois_from_args
        # doesn't trip on the (now consumed) title.
        args.doi = resolved_doi
        args.title = None

    loaded = _load_dois_from_args(args)
    if isinstance(loaded, dict):
        _emit(loaded)
        sys.exit(EXIT_VALIDATION)
    dois: list[str] = loaded

    # If a positional DOI argument was provided but it's actually an existing file,
    # treat it as a batch file containing DOIs (one per line)
    if args.doi is not None and not args.title and not args.batch:
        doi_as_file = Path(args.doi)
        if doi_as_file.is_file():
            try:
                with open(doi_as_file, 'r', encoding='utf-8') as f:
                    dois_from_file = [line.strip() for line in f if line.strip()]
                if dois_from_file:
                    dois = dois_from_file
            except Exception:
                # If we can't read the file, fall back to the original dois list
                pass

    # Idempotency replay — before any network I/O.
    if args.idempotency_key:
        cached = _idem_load(out_dir, args.idempotency_key)
        if cached is not None:
            # Re-stamp meta so the replayed envelope still reports current latency / request id.
            cached_meta = cached.get("meta", {}) or {}
            cached_meta.update({
                "request_id": _request_id,
                "latency_ms": _now_ms(),
                "replayed_from_idempotency_key": args.idempotency_key,
            })
            cached["meta"] = cached_meta
            _emit(cached)
            # Exit code mirrors the cached envelope's outcome.
            if cached.get("ok") is True:
                sys.exit(EXIT_SUCCESS)
            if cached.get("ok") == "partial":
                sys.exit(_decide_exit(cached.get("data", {}).get("results", [])))
            sys.exit(EXIT_VALIDATION if cached.get("error", {}).get("code") == "validation_error" else EXIT_UNRESOLVED)

    sources_list = [s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None
    results: list[dict] = []
    for d in dois:
        r = fetch(
            d,
            out_dir,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
            timeout=args.timeout,
            sources=sources_list,
        )
        results.append(r)
        if _stream and _format == "json":
            _emit_ndjson({"ok": bool(r.get("success")), "data": r, "meta": _meta()})

    succeeded = sum(1 for r in results if r.get("success"))
    total = len(results)
    failed = total - succeeded

    if succeeded == total:
        ok_flag: bool | str = True
    elif succeeded == 0:
        ok_flag = False
    else:
        ok_flag = "partial"

    data = {
        "results": results,
        "summary": {
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
        },
        "next": _next_hints(results, args),
    }

    sources_tried_union = sorted({s for r in results for s in r.get("sources_tried", [])})
    meta_extra = {"sources_tried": sources_tried_union}
    if not EMAIL:
        meta_extra["unpaywall_skipped"] = True
    if title_resolution is not None:
        meta_extra["title_resolution"] = title_resolution

    if ok_flag is False:
        # Total failure of a single-DOI call — downgrade to an error envelope
        # when the single result has an error with a code, so agents see
        # {ok:false, error:{...}} for the simple case.
        if total == 1 and results[0].get("error"):
            err = results[0]["error"]
            envelope = _envelope_err(
                err.get("code", "internal_error"),
                err.get("message", "failed"),
                retryable=err.get("retryable", False),
                **{k: v for k, v in err.items() if k not in ("code", "message", "retryable")},
                doi=results[0]["doi"],
                sources_tried=results[0].get("sources_tried", []),
            )
            envelope["meta"].update(meta_extra)
        else:
            envelope = _envelope_ok(data, ok=False, meta_extra=meta_extra)
    else:
        envelope = _envelope_ok(data, ok=ok_flag, meta_extra=meta_extra)

    # Stream mode already emitted per-item lines; final envelope still goes out as a summary.
    if _stream and _format == "json":
        print(_dump_json({"summary": data["summary"], "meta": envelope["meta"], "next": data["next"], "ok": ok_flag}), flush=True)
    else:
        _emit(envelope)

    # Store idempotency sidecar on completion (even for partial — replay returns same shape).
    if args.idempotency_key:
        _idem_store(out_dir, args.idempotency_key, envelope)

    sys.exit(_decide_exit(results))

# ===========================================================================
# TITLE RECOVERY V4 — PATTERN-DRIVEN + DIRECT TITLE DOWNLOAD (ADDITIVE)
# ===========================================================================
# The original implementation is preserved above. This layer fixes a key
# limitation of title-only searches: resolving a title to a DOI is not the same
# as finding a PDF. We therefore:
#   1) generate multiple deterministic title variants;
#   2) query independent metadata indexes concurrently;
#   3) keep ALL good candidates instead of only the first result;
#   4) rank candidates using title + year + author + journal signals;
#   5) optionally consult subscription APIs when keys are configured;
#   6) search NASA NTRS, which is particularly valuable for the project's
#      microgravity / space-biology corpus;
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from title_match import (  # noqa: E402  (re-exportado: run_parallel e testes usam fetch.X)
    _author_similarity, _candidate_key, _classify_low_confidence, _deep_find_pdf_urls,
    _extract_first_year, _match_records, _merge_candidate_meta, _norm_match_text,
    _rank_title_candidates, _rank_title_recovery_candidates, _title_similarity,
    _title_variants, _token_jaccard, _year_similarity,
    _MIN_TITLE_LEN, TITLE_GAP_MIN, TITLE_SCORE_MIN, TITLE_VARIANT_COUNT,
)
from reporting import (  # noqa: E402  (re-exportado: run_parallel usa fetch.generate_detailed_report)
    generate_detailed_report, _write_title_report, _decide_exit, _next_hints, _default_format,
    EXIT_SUCCESS, EXIT_UNRESOLVED, EXIT_TRANSPORT, EXIT_VALIDATION, EXIT_AUTH,
)
from sources_repositories import (  # noqa: E402  (re-exportado: testes usam fetch.try_*)
    try_base_search, try_datacite, try_doaj, try_dryad, try_fatcat, try_figshare,
    try_hal, try_ntrs, try_openaire, try_zenodo, _ntrs_pdf_urls_for_record,
)
from sources_pubmed import (  # noqa: E402  (re-exportado: testes usam fetch.try_*)
    try_europe_pmc, try_europe_pmc_by_doi, try_europe_pmc_links, try_pmc,
    try_pmc_idconv, try_pmcid_from_pmid, try_pmid, _norm_pmcid,
)
from sources_openaccess import (  # noqa: E402  (re-exportado: testes usam fetch.try_*)
    try_core, try_oa_button, try_openalex, try_openalex_content, try_osti,
    try_semantic_scholar, try_semantic_scholar_copy_by_title, try_unpaywall,
)
from sources_crossref import (  # noqa: E402  (re-exportado: testes usam fetch.try_*)
    try_acl_anthology, try_arxiv, try_arxiv_metadata, try_biorxiv,
    try_crossref_links, try_crossref_title, try_doi_resolver, try_paperdl,
    try_semantic_scholar_match, try_wayback,
)
from sources_shadow import (  # noqa: E402  (re-exportado: testes usam fetch.try_*)
    try_annas_archive, try_libgen, try_scihub,
)
from doi_input import (  # noqa: E402  (re-exportado: run_parallel usa fetch._load_dois_and_titles_from_file)
    _identifier_to_doi, _load_dois_and_titles_from_file, _read_text_any,
    _ARXIV_ID_RE, _DOI_RE, _PMCID_RE, _PMID_RE, DOI_PATTERN,
)

TITLE_RECOVERY_VERSION = "4.0.0"
TITLE_RECOVERY_SOURCE_TIMEOUT = max(3, int(os.environ.get("PAPER_FETCH_TITLE_SOURCE_TIMEOUT", "6")))
TITLE_RECOVERY_WORKERS = max(2, int(os.environ.get("PAPER_FETCH_TITLE_SOURCE_WORKERS", "6")))
TITLE_STRONG_THRESHOLD = float(os.environ.get("PAPER_FETCH_TITLE_STRONG_THRESHOLD", "0.90"))
# More permissive than the previous resolver, but still bibliographically
# conservative. This allows old/format-shifted titles to resolve when the
# candidate is demonstrably the same work or strongly corroborated.
TITLE_ACCEPT_THRESHOLD = float(os.environ.get("PAPER_FETCH_TITLE_THRESHOLD", "0.75"))
TITLE_DIRECT_THRESHOLD = float(os.environ.get("PAPER_FETCH_TITLE_DIRECT_THRESHOLD", "0.75"))
TITLE_AMBIGUOUS_MARGIN = float(os.environ.get("PAPER_FETCH_TITLE_AMBIGUOUS_MARGIN", "0.02"))

# Optional paid/institutional APIs. They are skipped when the key is absent.
WOS_API_KEY = os.environ.get("WOS_API_KEY", "").strip()
SCOPUS_API_KEY = os.environ.get("SCOPUS_API_KEY", "").strip()
SPRINGER_API_KEY = os.environ.get("SPRINGER_API_KEY", "").strip()
SPRINGER_OA_API_KEY = os.environ.get("SPRINGER_OA_API_KEY", "").strip()
IEEE_API_KEY = os.environ.get("IEEE_API_KEY", "").strip()
NCBI_EMAIL = os.environ.get("NCBI_EMAIL", "").strip() or EMAIL
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "").strip()












def _ncb_query_url(query: str) -> str:
    params = {
        "db": "pubmed",
        "term": f"{query}[Title]",
        "retmode": "json",
        "retmax": "10",
        "tool": "paper-fetch",
        "email": NCBI_EMAIL,
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    return "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(params)


def _try_pubmed_title_records(title: str, *, timeout: int) -> list[dict]:
    try:
        data = _get_json(_ncb_query_url(title), timeout=timeout)
        ids = ((data or {}).get("esearchresult") or {}).get("idlist") or []
        if not ids:
            return []
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(ids[:10]),
            "retmode": "xml",
            "tool": "paper-fetch",
            "email": NCBI_EMAIL,
        }
        if NCBI_API_KEY:
            fetch_params["api_key"] = NCBI_API_KEY
        xml_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(fetch_params)
        xml_text = _get_text(xml_url, timeout=timeout)
        root = ET.fromstring(xml_text)
        out: list[dict] = []
        for article in root.findall(".//PubmedArticle"):
            title_node = article.find(".//ArticleTitle")
            title_value = "".join(title_node.itertext()).strip() if title_node is not None else ""
            doi = ""
            for aid in article.findall(".//ArticleId"):
                if aid.attrib.get("IdType") == "doi" and aid.text:
                    doi = aid.text.strip()
                    break
            author = None
            first = article.find(".//Author")
            if first is not None:
                author = first.findtext("LastName") or first.findtext("CollectiveName")
            year = article.findtext(".//PubDate/Year") or article.findtext(".//PubDate/MedlineDate")
            out.append({
                "resolver": "pubmed",
                "doi": normalize_doi(doi),
                "title": title_value,
                "year": year,
                "author": author,
                "pmid": article.findtext(".//PMID"),
            })
        return out
    except Exception as exc:
        _progress("title_resolver_miss", resolver="pubmed", reason=str(exc))
        return []


def _try_ntrs_records(query: str, *, timeout: int) -> list[dict]:
    """NASA NTRS discovery, particularly useful for legacy space-biology papers."""
    base = os.environ.get("NTRS_API_BASE", "https://ntrs.nasa.gov/api").rstrip("/")
    params_options = [
        {"q": query, "page": 1, "pageSize": 10},
        {"query": query, "page": 1, "pageSize": 10},
    ]
    for params in params_options:
        try:
            url = base + "/citations/search?" + urllib.parse.urlencode(params)
            data = _get_json(url, timeout=timeout)
            raw_items = []
            if isinstance(data, dict):
                for key in ("results", "citations", "items", "data"):
                    value = data.get(key)
                    if isinstance(value, list):
                        raw_items = value
                        break
                    if isinstance(value, dict):
                        for key2 in ("results", "citations", "items", "data"):
                            if isinstance(value.get(key2), list):
                                raw_items = value[key2]
                                break
                        if raw_items:
                            break
            out: list[dict] = []
            for item in raw_items[:10]:
                if not isinstance(item, dict):
                    continue
                title_value = item.get("title") or item.get("documentTitle") or item.get("name")
                doi = item.get("doi") or item.get("DOI")
                citation_id = item.get("id") or item.get("citationId")
                author = None
                authors = item.get("authors") or item.get("author")
                if isinstance(authors, list) and authors:
                    a0 = authors[0]
                    author = a0.get("name") if isinstance(a0, dict) else str(a0)
                out.append({
                    "resolver": "ntrs",
                    "doi": normalize_doi(str(doi or "")),
                    "title": title_value,
                    "year": item.get("publicationDate") or item.get("published"),
                    "author": author,
                    "ntrs_id": citation_id,
                    "raw": item,
                })
            if out:
                return out
        except Exception:
            continue
    return []


def _try_wos_title_records(title: str, *, timeout: int) -> list[dict]:
    if not WOS_API_KEY:
        return []
    try:
        url = "https://api.clarivate.com/apis/wos-starter/v1/documents?" + urllib.parse.urlencode({
            "q": f'TI=("{title}")',
            "limit": 10,
        })
        data = _get_json_with_headers(url, timeout=timeout, headers={"X-ApiKey": WOS_API_KEY})
        docs = (data or {}).get("hits") or (data or {}).get("documents") or []
        out = []
        for doc in docs:
            out.append({
                "resolver": "web_of_science",
                "doi": normalize_doi(str(doc.get("doi") or ((doc.get("identifiers") or {}).get("doi") if isinstance(doc.get("identifiers"), dict) else ""))),
                "title": doc.get("title") or doc.get("displayName"),
                "year": doc.get("publicationYear") or doc.get("year"),
                "author": doc.get("firstAuthor") or doc.get("author"),
                "journal": doc.get("sourceTitle") or doc.get("journal"),
            })
        return out
    except Exception as exc:
        _progress("title_resolver_miss", resolver="web_of_science", reason=str(exc))
        return []


def _try_scopus_title_records(title: str, *, timeout: int) -> list[dict]:
    if not SCOPUS_API_KEY:
        return []
    try:
        params = {
            "query": f'TITLE("{title}")',
            "count": 10,
            "httpAccept": "application/json",
        }
        url = "https://api.elsevier.com/content/search/scopus?" + urllib.parse.urlencode(params)
        data = _get_json_with_headers(url, timeout=timeout, headers={"X-ELS-APIKey": SCOPUS_API_KEY})
        entries = ((data or {}).get("search-results") or {}).get("entry") or []
        out = []
        for e in entries:
            out.append({
                "resolver": "scopus",
                "doi": normalize_doi(str(e.get("prism:doi") or "")),
                "title": e.get("dc:title"),
                "year": e.get("prism:coverDate") or e.get("prism:publicationDate"),
                "author": e.get("dc:creator"),
                "journal": e.get("prism:publicationName"),
                "eid": e.get("eid"),
                "pii": e.get("pii"),
            })
        return out
    except Exception as exc:
        _progress("title_resolver_miss", resolver="scopus", reason=str(exc))
        return []


def _try_springer_title_records(title: str, *, timeout: int) -> list[dict]:
    key = SPRINGER_OA_API_KEY or SPRINGER_API_KEY
    if not key:
        return []
    try:
        params = {
            "api_key": key,
            "q": f'title:"{title}"',
            "s": 1,
            "p": 10,
            "format": "json",
        }
        url = "https://api.springernature.com/openaccess/json?" + urllib.parse.urlencode(params)
        data = _get_json(url, timeout=timeout)
        records = (data or {}).get("records") or []
        if not records:
            params["q"] = f'title:"{title}"'
            url = "https://api.springernature.com/meta/v2/json?" + urllib.parse.urlencode(params)
            data = _get_json(url, timeout=timeout)
            records = (data or {}).get("records") or []
        out = []
        for r in records:
            out.append({
                "resolver": "springer",
                "doi": normalize_doi(str(r.get("doi") or r.get("identifier") or "")),
                "title": r.get("title"),
                "year": r.get("publicationDate"),
                "author": ((r.get("creators") or [{}])[0].get("creator") if isinstance(r.get("creators"), list) and r.get("creators") else None),
                "journal": r.get("journalTitle"),
                "url": r.get("url"),
                "open_access": True,
            })
        return out
    except Exception as exc:
        _progress("title_resolver_miss", resolver="springer", reason=str(exc))
        return []


def _try_ieee_title_records(title: str, *, timeout: int) -> list[dict]:
    if not IEEE_API_KEY:
        return []
    try:
        params = {
            "article_title": title,
            "apikey": IEEE_API_KEY,
            "start_record": 1,
            "max_records": 10,
        }
        url = "https://ieeexploreapi.ieee.org/api/v1/search/articles?" + urllib.parse.urlencode(params)
        data = _get_json(url, timeout=timeout)
        records = (data or {}).get("articles") or []
        out = []
        for r in records:
            out.append({
                "resolver": "ieee",
                "doi": normalize_doi(str(r.get("doi") or "")),
                "title": r.get("title"),
                "year": r.get("publication_year"),
                "author": ((r.get("authors") or [{}])[0].get("full_name") if isinstance(r.get("authors"), list) and r.get("authors") else None),
                "article_number": r.get("article_number"),
                "access_type": r.get("access_type"),
            })
        return out
    except Exception as exc:
        _progress("title_resolver_miss", resolver="ieee", reason=str(exc))
        return []


def _search_title_source(source: str, variant: str, timeout: int) -> list[dict]:
    """Single-source title search adapter used by the concurrent resolver."""
    try:
        if source == "crossref":
            _, _, rows = try_crossref_title(variant, timeout=timeout)
            return [{"resolver": "crossref", **r} for r in rows]
        if source == "semantic_scholar":
            doi, meta = try_semantic_scholar_match(variant, timeout=timeout)
            return [{"resolver": "semantic_scholar", **meta}] if doi else []
        if source == "openalex":
            params = {
                "search": variant,
                "per-page": 10,
                "select": "id,doi,title,publication_year,authorships,primary_location",
            }
            if os.environ.get("OPENALEX_MAILTO") or EMAIL:
                params["mailto"] = os.environ.get("OPENALEX_MAILTO") or EMAIL
            data = _get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params), timeout=timeout)
            out = []
            for item in (data or {}).get("results") or []:
                authorships = item.get("authorships") or []
                out.append({
                    "resolver": "openalex",
                    "doi": normalize_doi(str(item.get("doi") or "").replace("https://doi.org/", "").replace("http://doi.org/", "")),
                    "title": item.get("title"),
                    "year": item.get("publication_year"),
                    "author": ((authorships[0].get("author") or {}).get("display_name") if authorships else None),
                    "journal": ((item.get("primary_location") or {}).get("source") or {}).get("display_name"),
                })
            return out
        if source == "europe_pmc":
            params = {"query": f'TITLE:"{variant}"', "format": "json", "pageSize": 10}
            data = _get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params), timeout=timeout)
            return [{
                "resolver": "europe_pmc",
                "doi": normalize_doi(str(x.get("doi") or "")),
                "title": x.get("title"),
                "year": x.get("pubYear"),
                "author": x.get("authorString"),
                "journal": x.get("journalTitle"),
                "pmid": x.get("pmid"),
                "pmcid": x.get("pmcid"),
            } for x in ((data or {}).get("resultList", {}).get("result") or [])]
        if source == "pubmed":
            return _try_pubmed_title_records(variant, timeout=timeout)
        repository_resolvers = {
            "openaire": try_openaire, "hal": try_hal, "zenodo": try_zenodo,
            "datacite": try_datacite, "doaj": try_doaj,
        }
        if source in repository_resolvers:
            urls, meta, raw = repository_resolvers[source](title=variant, timeout=timeout)
            if not meta.get("title"):
                return []
            return [{"resolver": source, **meta, "download_url": urls, "raw": raw}]
        if source == "ntrs":
            return _try_ntrs_records(variant, timeout=timeout)
        if source == "wos":
            return _try_wos_title_records(variant, timeout=timeout)
        if source == "scopus":
            return _try_scopus_title_records(variant, timeout=timeout)
        if source == "springer":
            return _try_springer_title_records(variant, timeout=timeout)
        if source == "ieee":
            return _try_ieee_title_records(variant, timeout=timeout)
    except Exception as exc:
        _progress("title_resolver_miss", resolver=source, reason=str(exc))
    return []


def _resolve_title_v4(title: str, *, timeout: int) -> tuple[str | None, dict]:
    """Resolve title via multi-variant, multi-index, evidence-based ranking."""
    variants = _title_variants(title)
    if not variants:
        return None, {"query": title, "resolver": "v4", "low_confidence": True, "low_confidence_reason": "invalid_title"}

    sources = [
        "crossref", "semantic_scholar", "openalex", "europe_pmc", "pubmed",
        "openaire", "hal", "zenodo", "datacite", "doaj", "ntrs",
    ]
    if WOS_API_KEY:
        sources.append("wos")
    if SCOPUS_API_KEY:
        sources.append("scopus")
    if SPRINGER_API_KEY or SPRINGER_OA_API_KEY:
        sources.append("springer")
    if IEEE_API_KEY:
        sources.append("ieee")

    records: list[dict] = []
    jobs = []
    with ThreadPoolExecutor(max_workers=min(TITLE_RECOVERY_WORKERS, len(sources))) as pool:
        # First pass uses the full title only; this minimizes requests for good matches.
        for source in sources:
            jobs.append(pool.submit(_inherit_budget(_search_title_source), source, variants[0], min(timeout, TITLE_RECOVERY_SOURCE_TIMEOUT)))
        for fut in as_completed(jobs):
            try:
                records.extend(fut.result() or [])
            except Exception:
                pass

    ranked = _rank_title_recovery_candidates(title, records)

    # If no strong candidate exists, use title variants progressively. This is
    # the important rescue path for punctuation-heavy, subtitle-heavy, and old titles.
    if not ranked or float(ranked[0].get("rank_score") or 0.0) < TITLE_STRONG_THRESHOLD:
        for variant in variants[1:]:
            if len(ranked) and float(ranked[0].get("rank_score") or 0.0) >= TITLE_STRONG_THRESHOLD:
                break
            with ThreadPoolExecutor(max_workers=min(TITLE_RECOVERY_WORKERS, len(sources))) as pool:
                futures = [pool.submit(_inherit_budget(_search_title_source), source, variant, min(timeout, TITLE_RECOVERY_SOURCE_TIMEOUT)) for source in sources]
                for fut in as_completed(futures):
                    try:
                        records.extend(fut.result() or [])
                    except Exception:
                        pass
            ranked = _rank_title_recovery_candidates(title, records)

    tried = sorted({r.get("resolver") for r in records if r.get("resolver")})
    resolution = {
        "query": title,
        "resolver": "v4_multi_variant",
        "resolver_version": TITLE_RECOVERY_VERSION,
        "variants": variants,
        "resolvers_tried": tried,
        "candidates": ranked[:15],
        "low_confidence": True,
    }
    if not ranked:
        resolution["low_confidence_reason"] = "no_candidate"
        _progress("title_resolve_miss", query=title, resolver="v4_multi_variant")
        return None, resolution

    best = ranked[0]
    score = float(best.get("rank_score") or 0.0)
    second = float(ranked[1].get("rank_score") or 0.0) if len(ranked) > 1 else 0.0
    gap = score - second
    resolution.update({
        "resolved_doi": best.get("doi"),
        "resolved_title": best.get("title"),
        "match_score": score,
        "title_similarity": best.get("title_similarity"),
        "candidate_gap": round(gap, 6),
        "resolver_selected": best.get("resolver"),
    })

    direct_urls = _deep_find_pdf_urls(best.get("raw") or best)

    if not best.get("doi") and score < TITLE_DIRECT_THRESHOLD:
        resolution["low_confidence_reason"] = "top_candidate_without_doi"
        return None, resolution

    if score < TITLE_ACCEPT_THRESHOLD:
        resolution["low_confidence_reason"] = "title_similarity_below_threshold"
        return None, resolution

    if gap < TITLE_AMBIGUOUS_MARGIN and score < TITLE_STRONG_THRESHOLD:
        # Allow an ambiguous candidate only when at least two independent
        # sources corroborate it. Otherwise keep the old conservative behavior.
        if int(best.get("source_count") or 1) < 2:
            resolution["low_confidence_reason"] = "ambiguous_top_candidates"
            return None, resolution

    resolution["direct_pdf_candidates"] = direct_urls[:12]
    resolution["low_confidence"] = False
    _progress(
        "title_resolve_hit",
        query=title,
        resolver=best.get("resolver"),
        doi=best.get("doi"),
        title=best.get("title"),
        score=score,
    )
    return (
        normalize_doi(str(best["doi"])) if best.get("doi") else None,
        resolution,
    )


# Install the V4 resolver last so existing callers transparently use it.
_resolve_title_v4_previous = _resolve_title
_resolve_title = _resolve_title_v4




def fetch_title_direct(
    title: str,
    out_dir: Path,
    *,
    timeout: int = 15,
    overwrite: bool = False,
    sources: list[str] | None = None,
) -> dict:
    """Resolve and download a title directly, even when no DOI is available.

    This is the critical fallback for title-only inputs.  It searches every
    configured title source, keeps candidates with strong bibliographic
    similarity, extracts any direct PDF/landing URLs available in the source
    records, and only then falls back to DOI-level fetching.

    The function intentionally treats "similar title" as a bibliographic
    variation of the requested work, not merely a paper on the same topic.
    """
    variants = _title_variants(title)
    if not variants:
        return {
            "searched_title": title,
            "success": False,
            "error": {"code": "title_resolve_failed", "message": "Invalid title"},
        }

    allowed = {x.strip().lower() for x in sources} if sources else None
    source_names = [
        "crossref",
        "semantic_scholar",
        "openalex",
        "europe_pmc",
        "pubmed",
        "openaire",
        "hal",
        "zenodo",
        "datacite",
        "doaj",
        "ntrs",
        "springer",
        "wos",
        "scopus",
        "ieee",
    ]
    if allowed is not None:
        source_names = [s for s in source_names if s in allowed]

    records: list[dict] = []

    # Search all title variants, not just the raw title. This is deliberately
    # additive: older indexes frequently omit subtitles or normalize punctuation.
    for variant_index, variant in enumerate(variants, start=1):
        if _deadline_exceeded():
            break
        try:
            with ThreadPoolExecutor(
                max_workers=min(TITLE_RECOVERY_WORKERS, max(1, len(source_names)))
            ) as pool:
                futs = [
                    pool.submit(
                        _inherit_budget(_search_title_source),
                        source,
                        variant,
                        min(timeout, TITLE_RECOVERY_SOURCE_TIMEOUT),
                    )
                    for source in source_names
                ]
                for fut in as_completed(futs):
                    try:
                        records.extend(fut.result() or [])
                    except Exception as exc:
                        _progress(
                            "title_resolver_miss",
                            resolver="direct",
                            variant=variant_index,
                            reason=str(exc),
                        )
        except Exception as exc:
            _progress("title_direct_search_error", variant=variant_index, error=str(exc))

        ranked = _rank_title_recovery_candidates(title, records)
        if ranked and float(ranked[0].get("rank_score") or 0.0) >= TITLE_STRONG_THRESHOLD:
            break

    ranked = _rank_title_recovery_candidates(title, records)
    tried = sorted({r.get("resolver") for r in records if r.get("resolver")})

    if not ranked:
        return {
            "searched_title": title,
            "success": False,
            "search_method": "title_direct_all_sources",
            "title_resolution": {
                "resolver": "title_direct_all_sources",
                "resolvers_tried": tried,
                "low_confidence": True,
                "low_confidence_reason": "no_candidate",
            },
            "error": {
                "code": "title_not_found",
                "message": f"Nenhum candidato bibliográfico encontrado para: {title}",
            },
        }

    # Build a ranked list of PDF candidates. A candidate can have:
    # - a DOI that can be passed through the mature DOI fetch chain;
    # - one or more explicit PDF URLs;
    # - a landing/download URL that may resolve to a PDF.
    pdf_candidates: list[tuple[str, str, dict]] = []
    seen_urls: set[tuple[str, str]] = set()
    seen_dois: set[str] = set()

    for rec in ranked[:25]:
        score = float(rec.get("rank_score") or 0.0)
        if score < TITLE_DIRECT_THRESHOLD:
            continue

        source = str(rec.get("resolver") or "unknown")

        urls: list[str] = []

        # Pull URLs from the normalized record and from raw API payloads.
        urls.extend(rec.get("pdf_candidates", []))
        urls.extend(_deep_find_pdf_urls(rec))
        raw = rec.get("raw")
        if raw is not None:
            urls.extend(_deep_find_pdf_urls(raw))

        # Explicit common URL fields.
        for field in (
            "pdf_url",
            "download_url",
            "url",
            "landing_page_url",
            "fulltext_url",
            "full_text_url",
            "content_url",
        ):
            value = rec.get(field)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                urls.append(value)
            elif isinstance(value, list):
                urls.extend(
                    x for x in value
                    if isinstance(x, str) and x.startswith(("http://", "https://"))
                )

        # DOI recovery is preferred because the mature DOI pipeline knows
        # Unpaywall/OpenAlex/PMC/etc. better than the generic direct URL path.
        doi = normalize_doi(str(rec.get("doi") or ""))
        if doi and score >= TITLE_ACCEPT_THRESHOLD and doi not in seen_dois:
            seen_dois.add(doi)
            try:
                doi_result = _fetch_original(
                    doi,
                    out_dir,
                    dry_run=False,
                    overwrite=overwrite,
                    timeout=timeout,
                    sources=sources,
                )
                if doi_result.get("success"):
                    doi_result["searched_title"] = title
                    doi_result["resolved_doi"] = doi
                    doi_result["title_resolution"] = {
                        "resolver": source,
                        "record": {
                            k: v for k, v in rec.items()
                            if k != "raw"
                        },
                        "resolvers_tried": tried,
                        "match_score": score,
                    }
                    return doi_result
            except Exception as exc:
                _progress("title_direct_doi_fetch_error", doi=doi, error=str(exc))

        for url in urls:
            key = (source, url)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            pdf_candidates.append((source, url, rec))

    # Direct URL candidates are attempted after bibliographic ranking.
    # This permits title-only records with no DOI to be recovered.
    for source, url, rec in pdf_candidates:
        fname_meta = {
            "title": rec.get("title") or title,
            "year": rec.get("year"),
            "author": rec.get("author"),
            "doi": rec.get("doi"),
        }
        dest = out_dir / _filename(fname_meta)
        # The candidate's OWN claimed DOI (if any) is what we ask the PDF to
        # confirm — it is not independently known to be correct just because
        # this record ranked well against the query title, so record-level
        # trust is deliberately left False here.
        expected = {
            "doi": rec.get("doi"),
            "title": rec.get("title") or title,
            "author": rec.get("author"),
            "journal": rec.get("journal"),
            "year": rec.get("year"),
        }

        if not overwrite and dest.exists():
            cached = _read_identity_sidecar(dest)
            if cached is None:
                cached = _validate_downloaded_file(dest, expected=expected, record_doi_matched=False)
                if cached["identity_validated"]:
                    _write_identity_sidecar(dest, cached)
            if cached["identity_validated"]:
                return {
                    "searched_title": title,
                    "resolved_doi": normalize_doi(str(rec.get("doi") or "")) or None,
                    "success": True,
                    "source": "cache",
                    "file": str(dest),
                    "meta": fname_meta,
                    **_identity_result_fields(cached),
                }
            _quarantine_unvalidated_file(dest)

        try:
            if _download(url, dest, timeout=timeout) is None:
                verdict = _validate_downloaded_file(dest, expected=expected, record_doi_matched=False)
                _progress(
                    "validation_result", searched_title=title, source=source, url=url,
                    result="CONFIRMED" if verdict["identity_validated"] else "REJECTED",
                    method=verdict["validation_method"], expected_doi=expected.get("doi"),
                    detected_doi=verdict.get("detected_doi"), phase="title_direct",
                )
                if not verdict["identity_validated"]:
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                    continue
                _write_identity_sidecar(dest, verdict)
                return {
                    "searched_title": title,
                    "resolved_doi": normalize_doi(str(rec.get("doi") or "")) or None,
                    "success": True,
                    "source": source,
                    "pdf_url": url,
                    "file": str(dest),
                    "title_resolution": {
                        "resolver": source,
                        "record": {
                            k: v for k, v in rec.items()
                            if k != "raw"
                        },
                        "resolvers_tried": tried,
                        "match_score": rec.get("rank_score"),
                    },
                    **_identity_result_fields(verdict),
                }
        except Exception as exc:
            _progress(
                "title_direct_download_error",
                source=source,
                url=url,
                error=str(exc),
            )

    best = ranked[0]
    return {
        "searched_title": title,
        "success": False,
        "resolved_doi": normalize_doi(str(best.get("doi") or "")) or None,
        "search_method": "title_direct_all_sources",
        "title_resolution": {
            "resolver": best.get("resolver"),
            "resolvers_tried": tried,
            "match_score": best.get("rank_score"),
            "title_similarity": best.get("title_similarity"),
            "title_coverage": best.get("title_coverage"),
            "source_count": best.get("source_count"),
            "candidates": [
                {
                    k: v for k, v in rec.items()
                    if k != "raw"
                }
                for rec in ranked[:15]
            ],
        },
        "error": {
            "code": "title_candidate_pdf_unavailable",
            "message": (
                "Candidatos bibliográficos foram encontrados, "
                "mas nenhum PDF pôde ser recuperado."
            ),
        },
    }
_fetch_with_expanded_previous = fetch


def _fetch_traced(*args, **kwargs) -> dict:
    """Run the cascade and attach this item's per-source attempt tally."""
    result = _fetch_with_expanded_previous(*args, **kwargs)
    stats = _stats_snapshot()
    if stats:
        result["source_stats"] = stats
    return result


def fetch(doi: str, out_dir: Path, *, dry_run: bool, overwrite: bool, timeout: int, sources: list[str] | None = None) -> dict:
    result = _fetch_traced(
        doi, out_dir, dry_run=dry_run, overwrite=overwrite, timeout=timeout, sources=sources
    )
    if result.get("success") or dry_run or _deadline_exceeded():
        return result

    meta = result.get("meta") or {}
    title = meta.get("title")
    if not title:
        return result

    # Do not launch another round for already-terminal publisher/HTTP failures
    # unless an explicit recovery flag is enabled.
    if os.environ.get("PAPER_FETCH_TITLE_RECOVERY", "1").strip().lower() not in {"1", "true", "yes", "on"}:
        return result

    recovery = fetch_title_direct(title, out_dir, timeout=min(timeout, 12), overwrite=overwrite, sources=sources)
    if recovery.get("success"):
        recovery.setdefault("recovery", {})["from_doi"] = doi
        recovery["recovery"]["strategy"] = "canonical_title_repository_search"
        return recovery

    result.setdefault("recovery", recovery)
    return result


# HTTP helpers for optional authenticated metadata APIs used by TITLE RECOVERY V4.
def _get_text(url: str, *, timeout: int) -> str:
    return _get(url, accept="application/json,application/xml,text/plain,*/*", timeout=timeout).decode("utf-8", "replace")


def _get_json_with_headers(url: str, *, timeout: int, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        _emit(_envelope_err("internal_error", str(e)))
        sys.exit(EXIT_TRANSPORT)
