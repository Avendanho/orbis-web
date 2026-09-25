#!/usr/bin/env python3
"""HTTP PDF transport with bounded retries and per-host concurrency.

The legacy module/class names are retained for compatibility. Requests use
normal HTTP headers; alternate header/IP/path spoofing has been removed.
"""

from __future__ import annotations

import typing
import gzip
import ipaddress
import os
import random
import re
import shutil
import subprocess
import sys
import time
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

try:
    from curl_cffi import requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    try:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
    except ImportError:
        requests = None
        HTTPAdapter = None
        Retry = None
    CURL_CFFI_AVAILABLE = False


MAX_PDF_SIZE = 50 * 1024 * 1024  # 50 MB
PROBE_TIMEOUT = 3  # Fast 3s timeout per probe to avoid stalling worker threads

BROWSER_PROFILES = [
    {
        "name": "chrome_win",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/pdf,application/xhtml+xml,text/html;q=0.9,application/xml;q=0.8,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,pt-BR;q=0.8,pt;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Ch-Ua": chr(34) + "Not/A)Brand" + chr(34) + ";v=" + chr(34) + "8" + chr(34) + ", " + chr(34) + "Chromium" + chr(34) + ";v=" + chr(34) + "126" + chr(34) + ", " + chr(34) + "Google Chrome" + chr(34) + ";v=" + chr(34) + "126" + chr(34),
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": chr(34) + "Windows" + chr(34),
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Connection": "keep-alive",
        },
    },
    {
        "name": "chrome_mac",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/pdf,application/xhtml+xml,text/html;q=0.9,application/xml;q=0.8,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Ch-Ua": chr(34) + "Chromium" + chr(34) + ";v=" + chr(34) + "125" + chr(34) + ", " + chr(34) + "Not.A/Brand" + chr(34) + ";v=" + chr(34) + "24" + chr(34) + ", " + chr(34) + "Google Chrome" + chr(34) + ";v=" + chr(34) + "125" + chr(34),
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": chr(34) + "macOS" + chr(34),
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
            "Connection": "keep-alive",
        },
    },
    {
        "name": "safari_mac",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
            "Accept": "application/pdf,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
            "Connection": "keep-alive",
        },
    },
]

_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "metadata.aws.internal",
    "metadata",
}


def is_safe_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"scheme_not_allowed:{parsed.scheme}"
        if parsed.port is not None and parsed.port not in (80, 443):
            return False, f"port_not_allowed:{parsed.port}"
        host = (parsed.hostname or "").lower()
        if not host:
            return False, "empty_host"
        if host in _BLOCKED_HOSTS:
            return False, f"blocked_host:{host}"
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if (
                literal.is_private
                or literal.is_loopback
                or literal.is_link_local
                or literal.is_reserved
                or literal.is_multicast
                or literal.is_unspecified
            ):
                return False, f"private_ip:{literal}"
        return True, ""
    except Exception as exc:
        return False, f"url_parse_error:{exc}"


def validate_pdf_data(data: bytes) -> tuple[bool, bytes, str]:
    """Strictly validates that data is a complete, uncorrupted, parseable PDF."""
    if not data or len(data) < 1024:
        return False, b"", "file_too_small"

    # Decompress gzip payload if needed
    if len(data) >= 2 and data[:2] == bytes([0x1f, 0x8b]):
        try:
            data = gzip.decompress(data)
        except Exception as exc:
            return False, b"", f"gzip_decompress_error:{exc}"

    if len(data) > MAX_PDF_SIZE:
        return False, b"", "size_exceeded"

    if not data.startswith(b"%PDF"):
        return False, b"", "invalid_pdf_header"

    # In-memory parsing verification with pypdf (if available)
    try:
        import io
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        if len(reader.pages) == 0:
            return False, b"", "empty_pdf_pages"
        return True, data, ""
    except ImportError:
        pass
    except Exception as exc:
        if b"%%EOF" not in data[-8192:]:
            return False, b"", f"corrupted_pdf_structure:{exc}"

    # Check for EOF marker within the last 8192 bytes
    if b"%%EOF" not in data[-8192:]:
        return False, b"", "missing_eof_marker"

    return True, data, ""


@dataclass
class BypassResult:
    success: bool
    status_code: int
    data: bytes | None = None
    module_used: str = ""
    technique: str = ""
    error: str | None = None
    url: str = ""
    headers_used: dict[str, str] = field(default_factory=dict)


class GoByPASS403Engine:
    def __init__(
        self,
        timeout: int = 15,
        enable_curl: bool = True,
    ):
        self.timeout = timeout
        self.enable_curl = enable_curl and (shutil.which("curl") is not None)
        self._session: typing.Any = None
        self._success_cache = {}
        self._host_lock = threading.Lock()
        self._host_slots = {}
        
        self.proxies = None
        proxy_url = os.environ.get("PROXY_URL", "").strip()
        if proxy_url:
            self.proxies = {"http": proxy_url, "https": proxy_url}

        if requests is not None:
            if CURL_CFFI_AVAILABLE:
                self._session = requests.Session(impersonate="chrome", proxies=self.proxies)  # type: ignore
            else:
                self._session = requests.Session()
                adapter = HTTPAdapter(pool_connections=25, pool_maxsize=25, max_retries=1)  # type: ignore
                self._session.mount("http://", adapter)
                self._session.mount("https://", adapter)
                if self.proxies:
                    self._session.proxies.update(self.proxies)

    def _get_base_headers(self, url: str) -> dict[str, str]:
        profile = random.choice(BROWSER_PROFILES)
        headers = dict(profile["headers"])
        parsed = urllib.parse.urlparse(url)
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

        # Elsevier / ScienceDirect Official API Authentication Headers
        if "api.elsevier.com" in url:
            els_key = os.environ.get("ELSEVIER_API_KEY", "").strip()
            if els_key:
                headers["X-ELS-APIKey"] = els_key
            els_inst = os.environ.get("ELSEVIER_INST_TOKEN", "").strip()
            if els_inst:
                headers["X-ELS-Insttoken"] = els_inst
            els_bearer = os.environ.get("ELSEVIER_BEARER_TOKEN", "").strip()
            if els_bearer:
                headers["Authorization"] = f"Bearer {els_bearer}"

        # Polite pool mailto headers for CrossRef & OpenAlex
        cr_mailto = os.environ.get("CROSSREF_MAILTO", "").strip()
        if cr_mailto and "crossref.org" in url:
            headers["User-Agent"] = f"{headers.get('User-Agent', '')} (mailto:{cr_mailto})"

        oa_mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
        if oa_mailto and "openalex.org" in url:
            headers["User-Agent"] = f"{headers.get('User-Agent', '')} (mailto:{oa_mailto})"

        # Wiley Text and Data Mining token (legitimate licensed access).
        if "api.wiley.com" in url:
            wiley_token = os.environ.get("WILEY_TDM_TOKEN", "").strip()
            if wiley_token:
                headers["Wiley-TDM-Client-Token"] = wiley_token

        return headers

    def _is_valid_pdf(self, data: bytes) -> bool:
        ok, _, _ = validate_pdf_data(data)
        return ok

    def _exec_curl(
        self,
        url: str,
        headers: dict[str, str],
        *,
        timeout: int,
        raw_path: bool = True,
    ) -> tuple[int, bytes, str]:
        if not self.enable_curl:
            return 0, b"", "curl_not_available"

        cmd = [
            "curl",
            "-sSL",
            "--compressed",
            "--max-time",
            str(timeout),
            "-w",
            chr(10) + "%{http_code}",
        ]
        if raw_path:
            cmd.append("--path-as-is")

        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])

        cmd.append(url)

        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout + 2,
            )
            out = res.stdout
            if not out:
                return 0, b"", res.stderr.decode("utf-8", "ignore")

            parts = out.rsplit(bytes([10]), 1)
            if len(parts) == 2:
                body, code_str = parts[0], parts[1].strip().decode("ascii", "ignore")
                try:
                    status_code = int(code_str)
                except ValueError:
                    status_code = 200 if body.startswith(b"%PDF") else 0
                return status_code, body, ""
            return 200, out, ""
        except Exception as exc:
            return 0, b"", str(exc)

    def _exec_requests(
        self,
        url: str,
        headers: dict[str, str],
        *,
        timeout: int,
        method: str = "GET",
    ) -> tuple[int, bytes, str]:
        if self._session is None:
            return 0, b"", "requests_not_installed"

        try:
            if method.upper() == "GET":
                resp = self._session.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=True,
                )
            elif method.upper() == "POST":
                resp = self._session.post(
                    url,
                    headers=headers,
                    data=b"",
                    timeout=timeout,
                    allow_redirects=True,
                )
            else:
                resp = self._session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=True,
                )

            return resp.status_code, resp.content, ""
        except Exception as exc:
            return 0, b"", str(exc)

    def execute_request(
        self, url: str, *, timeout: int | None = None,
        custom_headers: dict[str, str] | None = None, require_pdf: bool = False,
    ) -> BypassResult:
        """Bounded ordinary HTTP attempts, with at most two active calls per host.

        Do not multiply the request timeout by URL/header mutations. Browser
        recovery remains the responsibility of fetch._download.
        """
        safe, reason = is_safe_url(url)
        if not safe:
            return BypassResult(False, 0, error=f"unsafe_url:{reason}", url=url)
        host = urllib.parse.urlsplit(url).hostname or ""
        with self._host_lock:
            slot = self._host_slots.setdefault(host, threading.BoundedSemaphore(2))
        budget = max(1, timeout or self.timeout)
        if not require_pdf:
            budget = min(budget, PROBE_TIMEOUT)
        with slot:
            deadline = time.monotonic() + budget
            headers = self._get_base_headers(url)
            headers.update(custom_headers or {})
            last_error = "network_error"
            status = 0
            attempts = [self._exec_requests]
            if self.enable_curl:
                attempts.append(self._exec_curl)
            for attempt in attempts:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    last_error = "timeout"
                    break
                status, data, error = attempt(url, headers, timeout=max(1, int(remaining)))
                if status in (200, 206) and data:
                    if not require_pdf or self._is_valid_pdf(data):
                        return BypassResult(True, status, data=data, module_used="http", url=url)
                    # HTML is handled as a landing page by fetch._download.
                    return BypassResult(False, status, error="not_a_pdf", url=url)
                last_error = f"http_{status}" if status else (error or "network_error")
                if status in (401, 403, 404, 429):
                    break
            return BypassResult(False, status, error=last_error, url=url)

    def download_pdf(
        self,
        url: str,
        dest: Path,
        *,
        timeout: int = 35,
    ) -> tuple[bool, str | None]:
        res = self.execute_request(url, timeout=timeout, require_pdf=True)
        if not res.success or not res.data:
            return False, res.error or "download_failed"

        valid, clean_data, reason = validate_pdf_data(res.data)
        if not valid:
            return False, reason

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_dest = dest.with_name(f".{dest.name}.tmp.{os.getpid()}_{random.randint(1000, 9999)}")
            tmp_dest.write_bytes(clean_data)
            tmp_dest.replace(dest)
            return True, None
        except OSError as exc:
            return False, f"io_error:{exc}"


default_engine = GoByPASS403Engine()


def bypass_get(url: str, *, timeout: int = 15, headers: dict[str, str] | None = None) -> BypassResult:
    return default_engine.execute_request(url, timeout=timeout, custom_headers=headers, require_pdf=False)


def bypass_download_pdf(url: str, dest: Path, *, timeout: int = 20) -> tuple[bool, str | None]:
    return default_engine.download_pdf(url, dest, timeout=timeout)
