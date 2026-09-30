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
import os
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

# Minimum spacing between requests to the same metadata API, from each
# service's published limit with some headroom. Without it, four articles in
# parallel plus the title-recovery searches burst past the limit, the API
# answers 429, and the cooldown above then takes the host out of the run for
# minutes — silently, for every article that follows.
MIN_INTERVAL = {
    "api.openalex.org": 0.12,          # 10 req/s with a key
    "api.semanticscholar.org": 1.05,   # 1 req/s with a key; the anonymous pool is shared by everyone
    "api.crossref.org": 0.1,           # polite pool
    "api.unpaywall.org": 0.1,
    "www.ebi.ac.uk": 0.1,              # Europe PMC REST
    "ncbi": 0.11,                      # 10 req/s with NCBI_API_KEY
}

# Stricter spacing when the service's key is missing: anonymous OpenAlex answers
# bursts well under its documented 10 req/s with 429, and NCBI allows 3 req/s
# per IP without a key — counted across all of its hosts together.
ANONYMOUS_INTERVAL = {
    "api.openalex.org": ("OPENALEX_API_KEY", 0.5),
    "ncbi": ("NCBI_API_KEY", 0.4),
}

# Hosts that share one limit. The ID converter (the door to the PMC bucket,
# the source that delivers most) and E-utilities are both NCBI.
PACE_GROUP = {
    "pmc.ncbi.nlm.nih.gov": "ncbi",
    "eutils.ncbi.nlm.nih.gov": "ncbi",
    "www.ncbi.nlm.nih.gov": "ncbi",
}

_lock = threading.Lock()
_hosts: dict[str, dict] = {}
_next_slot: dict[str, float] = {}


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


def _interval(key: str) -> float | None:
    anonymous = ANONYMOUS_INTERVAL.get(key)
    if anonymous and not os.environ.get(anonymous[0], "").strip():
        return anonymous[1]
    return MIN_INTERVAL.get(key)


def pace(url: str, *, max_wait: float | None = None) -> float:
    """Wait for this host's next request slot. Returns the seconds waited.

    Slots are reserved under the lock and slept outside it, so concurrent
    callers queue in order instead of all waking at once. ``max_wait`` bounds
    the sleep (the caller's remaining article budget); the slot is still taken.
    """
    key = PACE_GROUP.get(host_of(url), host_of(url))
    interval = _interval(key)
    if not interval:
        return 0.0
    with _lock:
        now = time.monotonic()
        slot = max(now, _next_slot.get(key, 0.0))
        _next_slot[key] = slot + interval
    wait = slot - now
    if max_wait is not None:
        wait = min(wait, max(0.0, max_wait))
    if wait > 0:
        time.sleep(wait)
    return max(0.0, wait)


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
        _next_slot.clear()
