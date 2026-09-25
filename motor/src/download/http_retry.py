"""Retry policy and per-host throttling shared by every HTTP call.

Two problems, one place to solve them:

* **Transient failures** (429, 500, 502, 503, 504, connection resets). These
  deserve a retry with exponential backoff and jitter, honouring
  ``Retry-After`` when the server sends it.
* **A host that is simply down or refusing us.** Retrying it on every one of
  200 articles wastes the whole run. After a few consecutive failures the
  host is put on a cooldown and skipped fast until it expires.

The backoff is about being a good client — it slows down when a server says
it is overloaded. It is not a way to keep pushing against a host that has
refused the request: 401/403/404 are never retried here.
"""
from __future__ import annotations

import email.utils
import random
import threading
import time
import urllib.parse

# Statuses worth trying again: the server said "not now", not "no".
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

DEFAULT_ATTEMPTS = 3
BASE_DELAY = 0.8          # seconds, doubled per attempt
MAX_DELAY = 20.0
MAX_RETRY_AFTER = 60.0    # a longer Retry-After means "come back another day"

# Cooldown ladder applied after consecutive failures from the same host.
COOLDOWN_AFTER_FAILURES = 4
COOLDOWN_SECONDS = (30.0, 120.0, 600.0)

_lock = threading.Lock()
_hosts: dict[str, dict] = {}


def host_of(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def parse_retry_after(value: str | None) -> float | None:
    """Seconds from a Retry-After header, in either of its two formats."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    import datetime

    now = datetime.datetime.now(tz=when.tzinfo) if when.tzinfo else datetime.datetime.now()
    return max(0.0, (when - now).total_seconds())


def backoff_delay(attempt: int, *, retry_after: float | None = None) -> float:
    """Delay before attempt ``attempt`` (0-based), with full jitter."""
    if retry_after is not None:
        return min(retry_after, MAX_RETRY_AFTER)
    window = min(MAX_DELAY, BASE_DELAY * (2 ** attempt))
    return random.uniform(window / 2, window)


def cooldown_remaining(url: str) -> float:
    """Seconds left before this host should be contacted again (0 = ready)."""
    host = host_of(url)
    if not host:
        return 0.0
    with _lock:
        state = _hosts.get(host)
        if not state:
            return 0.0
        return max(0.0, state.get("until", 0.0) - time.monotonic())


def host_ready(url: str) -> bool:
    return cooldown_remaining(url) <= 0.0


def note_success(url: str) -> None:
    host = host_of(url)
    if not host:
        return
    with _lock:
        _hosts.pop(host, None)


def note_failure(url: str, *, status: int | None = None, retry_after: float | None = None) -> float:
    """Record a transient failure. Returns the cooldown now in force (seconds)."""
    host = host_of(url)
    if not host:
        return 0.0
    with _lock:
        state = _hosts.setdefault(host, {"failures": 0, "until": 0.0, "last_status": None})
        state["failures"] += 1
        state["last_status"] = status
        cooldown = 0.0
        if retry_after:
            cooldown = min(retry_after, MAX_RETRY_AFTER)
        elif state["failures"] >= COOLDOWN_AFTER_FAILURES:
            step = min(state["failures"] - COOLDOWN_AFTER_FAILURES, len(COOLDOWN_SECONDS) - 1)
            cooldown = COOLDOWN_SECONDS[step]
        if cooldown:
            state["until"] = max(state["until"], time.monotonic() + cooldown)
        return cooldown


def snapshot() -> dict[str, dict]:
    """Hosts currently on cooldown, for logging and the dashboard."""
    now = time.monotonic()
    with _lock:
        return {
            host: {
                "failures": state["failures"],
                "last_status": state["last_status"],
                "cooldown_seconds": round(max(0.0, state["until"] - now), 1),
            }
            for host, state in _hosts.items()
            if state["until"] > now
        }


def reset() -> None:
    with _lock:
        _hosts.clear()
