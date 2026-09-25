#!/usr/bin/env python3
"""Fetch a publicly accessible PDF through CloakBrowser.

Usage:
    cloak_pdf.py <url> [timeout_seconds]

Stdout:
    Raw PDF bytes only.

Stderr:
    Diagnostic messages.

The helper uses CloakBrowser's Playwright-compatible synchronous API.
It navigates Chromium to the target URL and captures the final response,
avoiding cross-origin CORS failures from page-level fetch().
"""

from __future__ import annotations

import ipaddress
import os
import sys
import time
from urllib.parse import urlparse

MAX_PDF_SIZE = 50 * 1024 * 1024

_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "metadata.aws.internal",
    "metadata",
}


def _err(message: str) -> None:
    print(f"[cloak] {message}", file=sys.stderr, flush=True)


def _url_is_safe(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False, "scheme_not_allowed"

        if parsed.port is not None and parsed.port not in (80, 443):
            return False, "port_not_allowed"

        host = (parsed.hostname or "").lower()
        if not host:
            return False, "empty_host"

        if host in _BLOCKED_HOSTS:
            return False, "blocked_host"

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
                return False, "private_ip"
            return True, ""

        import socket

        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False, "dns_error"

        for info in infos:
            try:
                resolved = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue

            if (
                resolved.is_private
                or resolved.is_loopback
                or resolved.is_link_local
                or resolved.is_reserved
                or resolved.is_multicast
                or resolved.is_unspecified
            ):
                return False, "private_ip"

        return True, ""

    except ValueError:
        return False, "invalid_url"
    except Exception as exc:
        return False, f"url_check_error:{exc}"


def _wait_for_challenge(page, timeout_s: int) -> None:
    deadline = time.monotonic() + min(timeout_s, 40)
    time.sleep(1.5)

    while time.monotonic() < deadline:
        try:
            title = (page.title() or "").lower()
        except Exception:
            title = ""

        is_challenge = (
            "just a moment" in title
            or title.startswith("loading")
            or "checking your browser" in title
            or "attention required" in title
            or "cloudflare" in title
            or "ddos-guard" in title
            or "ddos" in title
            or "security check" in title
        )

        try:
            for frame in page.frames:
                frame_url = frame.url.lower()
                if any(k in frame_url for k in ("cloudflare", "turnstile", "ddos")):
                    is_challenge = True
                    try:
                        box = frame.locator("input[type=checkbox], .ctp-checkbox-label, #challenge-stage")
                        if box.count() > 0 and box.first.is_visible():
                            box.first.click()
                    except Exception:
                        pass
        except Exception:
            pass

        if not is_challenge and title:
            return

        time.sleep(1)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not (1 <= len(args) <= 2):
        _err("usage: cloak_pdf.py <url> [timeout_seconds] [--allow-html]")
        return 1

    url = args[0]

    try:
        timeout_s = max(5, int(args[1])) if len(args) == 2 else 60
    except ValueError:
        _err("timeout_seconds must be an integer")
        return 1

    safe, reason = _url_is_safe(url)
    if not safe:
        _err(f"refusing unsafe URL ({reason})")
        return 1

    try:
        from cloakbrowser import launch
    except ImportError as exc:
        _err(f"cloakbrowser import failed: {exc}")
        _err("install with: python -m pip install -U cloakbrowser")
        return 1

    headed = bool(os.environ.get("PAPER_FETCH_CLOAK_HEADED"))
    browser = None
    pw_instance = None

    try:
        try:
            from cloakbrowser import launch
            _err(f"launching CloakBrowser ({'headed' if headed else 'headless'})")
            browser = launch(
                headless=not headed,
                humanize=True,
            )
        except Exception as exc:
            _err(f"cloakbrowser launch failed ({exc}), falling back to Playwright Chromium...")
            from playwright.sync_api import sync_playwright
            pw_instance = sync_playwright().start()
            browser = pw_instance.chromium.launch(
                headless=not headed,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-infobars",
                ],
            )
            _err("launched Playwright Chromium successfully")

        page = browser.new_page()

        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}/"

        _err(f"opening origin: {origin}")
        
        # Otimização: abortar recursos pesados mantendo estilos para o desafio Cloudflare
        page.route("**/*", lambda route: route.abort() 
            if route.request.resource_type in ["media", "font"] 
            else route.continue_()
        )
        
        try:
            page.goto(
                origin,
                wait_until="domcontentloaded",
                timeout=timeout_s * 1000,
            )
        except Exception as exc:
            _err(f"origin navigation warning: {exc}")

        _wait_for_challenge(page, timeout_s)

        _err(f"navigating to target: {url}")

        response = None
        last_error = None

        for attempt in range(2):
            try:
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout_s * 1000,
                )
                break
            except Exception as exc:
                last_error = exc
                _err(f"target navigation attempt {attempt + 1} failed: {exc}")
                if attempt == 0:
                    time.sleep(2)

        # Aguardar resolução do desafio no destino
        _wait_for_challenge(page, timeout_s)

        body = None
        if response is not None:
            try:
                body = response.body()
            except Exception:
                body = None

        # Se o corpo inicial não for PDF (desafio recém-resolvido), re-navega com cookies
        if not body or not body.startswith(b"%PDF"):
            try:
                _err("re-fetching target with solved session...")
                res2 = page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                if res2 is not None:
                    response = res2
                    body = response.body()
            except Exception as exc:
                _err(f"re-fetch warning: {exc}")

            if not body or not body.startswith(b"%PDF"):
                try:
                    _err("requesting target directly via browser context API with cookies...")
                    req_ctx = getattr(page.context, "request", None)
                    if req_ctx:
                        api_res = req_ctx.get(url, timeout=timeout_s * 1000)
                        if api_res.ok:
                            api_body = api_res.body()
                            if api_body.startswith(b"%PDF"):
                                body = api_body
                                _err(f"successfully fetched PDF via context API ({len(body)} bytes)")
                except Exception as exc:
                    _err(f"context API fetch warning: {exc}")

        if response is None and body is None:
            _err(f"target navigation failed: {last_error}")
            return 1

        status = getattr(response, "status", None) if response else 200

        try:
            final_url = response.url or "" if response else url
        except Exception:
            final_url = url

        try:
            content_type = (response.headers.get("content-type") or "").lower() if response else ""
        except Exception:
            content_type = ""

        _err(
            f"target response: HTTP {status}, "
            f"content-type={content_type!r}, final_url={final_url!r}"
        )

        if not isinstance(body, (bytes, bytearray)):
            _err(f"unexpected response body type: {type(body)!r}")
            return 1

        body = bytes(body)

        if len(body) > MAX_PDF_SIZE:
            _err(f"response exceeds {MAX_PDF_SIZE} byte limit")
            return 1

        allow_html = "--allow-html" in sys.argv
        if not allow_html and not body.startswith(b"%PDF"):
            preview = body[:120].decode("utf-8", "replace").replace("\n", " ")
            _err(
                "browser returned a non-PDF response "
                f"(content-type={content_type!r}, bytes={len(body)}, "
                f"preview={preview!r})"
            )
            return 1

        sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()

        _err(f"success: {len(body)} bytes")
        return 0

    except Exception as exc:
        _err(f"failed: {exc}")
        return 1

    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception as exc:
                _err(f"browser close warning: {exc}")
        if pw_instance is not None:
            try:
                pw_instance.stop()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())