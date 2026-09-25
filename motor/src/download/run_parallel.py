#!/usr/bin/env python3
"""Multi-session parallel orchestrator for paper-fetch with clean, elegant terminal UI.

Features:
- Clean, non-flooded console display (suppresses raw JSON/debug traces).
- Informative status per item: attempt number, resolved database, outcome, and PDF filename.
- Visual dynamic progress bar with Unicode smoothing and live counters.
- Pixel-perfect box border alignment with ANSI code stripping and East Asian width handling.
- Instant skip: checks if a valid PDF already exists in cache/disk and skips without network calls.
- Anti-freeze failover: fast timeouts and staggered worker concurrency.
- Unified consolidated reporting: generates 'Relatório.txt' and 'Relatório_artigos_por_nome.txt'.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import re
import sys
import threading
import time
import unicodedata
import uuid
from collections import Counter
from pathlib import Path

# Add project root to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

import fetch as fetch_module
from bypass403 import validate_pdf_data
from fetch import (
    _filename,
    _load_dois_and_titles_from_file,
    _resolve_title,
    _slug,
    _write_title_report,
    generate_detailed_report,
    fetch,
)

import logging
for _log_name in ("libgen_api_enhanced", "libgen_api", "urllib3", "pypdf", "requests", "playwright"):
    logging.getLogger(_log_name).setLevel(logging.CRITICAL)

# Silence raw JSON events from fetch.py during parallel runs
fetch_module._format = "silent"


# ---------------------------------------------------------------------------
# Terminal Styling & Width Calculations
# ---------------------------------------------------------------------------

def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") == "1":
        return True
    return sys.stdout.isatty() or sys.stderr.isatty()


USE_COLOR = _use_color()


class Style:
    RESET = "[0m" if USE_COLOR else ""
    BOLD = "[1m" if USE_COLOR else ""
    DIM = "[2m" if USE_COLOR else ""
    ITALIC = "[3m" if USE_COLOR else ""
    UNDERLINE = "[4m" if USE_COLOR else ""

    # Foreground Colors
    BLACK = "[30m" if USE_COLOR else ""
    RED = "[31m" if USE_COLOR else ""
    GREEN = "[32m" if USE_COLOR else ""
    YELLOW = "[33m" if USE_COLOR else ""
    BLUE = "[34m" if USE_COLOR else ""
    MAGENTA = "[35m" if USE_COLOR else ""
    CYAN = "[36m" if USE_COLOR else ""
    WHITE = "[37m" if USE_COLOR else ""
    GRAY = "[90m" if USE_COLOR else ""

    # High Intensity Bold Colors
    B_RED = "[1;91m" if USE_COLOR else ""
    B_GREEN = "[1;92m" if USE_COLOR else ""
    B_YELLOW = "[1;93m" if USE_COLOR else ""
    B_BLUE = "[1;94m" if USE_COLOR else ""
    B_MAGENTA = "[1;95m" if USE_COLOR else ""
    B_CYAN = "[1;96m" if USE_COLOR else ""
    B_WHITE = "[1;97m" if USE_COLOR else ""


ANSI_REGEX = re.compile(r"\[[0-9;]*[a-zA-Z]")

# Code points of double-width symbols/emojis
EMOJI_DOUBLE_WIDTH = {0x2705, 0x274C, 0x26A1, 0x1F680, 0x1F4CA, 0x1F4C4, 0x23F1, 0x23F3, 0x2B50, 0x1F4E6, 0x1F4C2}


def visible_width(s: str) -> int:
    """Calculate the exact terminal printable width of a string by stripping ANSI codes."""
    clean = ANSI_REGEX.sub("", s)
    w = 0
    for ch in clean:
        code = ord(ch)
        if unicodedata.east_asian_width(ch) in ("W", "F") or code in EMOJI_DOUBLE_WIDTH:
            w += 2
        else:
            w += 1
    return w


def render_box(lines: list[str], border_color: str = Style.B_MAGENTA, inner_width: int = 66) -> str:
    """Render a perfectly aligned terminal box."""
    out = []
    top = f"{border_color}╭{'─' * (inner_width + 2)}╮{Style.RESET}"
    out.append(top)

    for line in lines:
        if line == "---SEP---":
            sep = f"{border_color}├{'─' * (inner_width + 2)}┤{Style.RESET}"
            out.append(sep)
        else:
            w = visible_width(line)
            pad_len = max(0, inner_width - w)
            formatted = f"{border_color}│{Style.RESET} {line}{' ' * pad_len} {border_color}│{Style.RESET}"
            out.append(formatted)

    bot = f"{border_color}╰{'─' * (inner_width + 2)}╯{Style.RESET}"
    out.append(bot)
    return "\n".join(out)


SOURCE_NAMES = {
    "unpaywall": "Unpaywall",
    "europe_pmc": "Europe PMC",
    "pmc": "PubMed Central",
    "openalex": "OpenAlex",
    "semantic_scholar": "Semantic Scholar",
    "scihub": "Sci-Hub",
    "libgen": "LibGen",
    "annas_archive": "Anna's Archive",
    "arxiv": "arXiv",
    "biorxiv": "bioRxiv",
    "medrxiv": "medRxiv",
    "crossref": "CrossRef",
    "cache": "Cache",
    "publisher_direct": "Publisher Direct",
    "elsevier": "Elsevier",
    "elsevier_api": "Elsevier API",
    "core": "CORE",
    "pmc_s3": "PMC (AWS)",
    "pmc_s3_xml": "PMC XML (AWS)",
}


def _format_source_name(src: str | None) -> str:
    if not src:
        return "Desconhecido"
    return SOURCE_NAMES.get(src.lower(), src.replace("_", " ").title())


def _format_duration(seconds: float) -> str:
    mins, secs = divmod(int(seconds), 60)
    if mins > 0:
        return f"{mins}m {secs:02d}s"
    return f"{seconds:.1f}s"


# ---------------------------------------------------------------------------
# Progress Bar & Thread-Safe UI Renderer
# ---------------------------------------------------------------------------

class TerminalProgressBar:
    def __init__(self, total: int):
        self.total = max(1, total)
        self.current = 0
        self.downloaded = 0
        self.cached = 0
        self.failed = 0
        self.sources_count: Counter[str] = Counter()
        self.start_time = time.monotonic()
        self.active_items: set[str] = set()
        self.lock = threading.Lock()
        self._last_update_time = time.monotonic()
        self._running = True
        self._ticker = threading.Thread(target=self._heartbeat, daemon=True)
        self._ticker.start()

    def _heartbeat(self) -> None:
        spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spin_i = 0
        while self._running:
            time.sleep(2.5)
            with self.lock:
                if not self._running or self.current >= self.total:
                    break
                if time.monotonic() - self._last_update_time >= 3.0:
                    icon = spinner[spin_i % len(spinner)]
                    spin_i += 1
                    active_n = len(self.active_items)
                    elapsed = time.monotonic() - self.start_time
                    act_str = f" ({active_n} threads ativas)" if active_n > 0 else ""
                    print(
                        f"  {Style.B_YELLOW}{icon}{Style.RESET} {Style.DIM}Buscando nas bases de dados científicas{act_str}... "
                        f"[{_format_duration(elapsed)}]{Style.RESET}",
                        flush=True,
                    )
                    self._last_update_time = time.monotonic()

    def stop(self) -> None:
        self._running = False

    def on_start(self, item_id: str) -> None:
        with self.lock:
            short_id = item_id[:35] + "..." if len(item_id) > 38 else item_id
            self.active_items.add(short_id)

    def retract(self, result: dict) -> None:
        """Undo one recorded outcome, for an item that is being tried again.

        Without this the second pass would count the same article twice and
        the bar would run past 100%.
        """
        with self.lock:
            self.current = max(0, self.current - 1)
            if result.get("success"):
                if result.get("skipped"):
                    self.cached = max(0, self.cached - 1)
                else:
                    self.downloaded = max(0, self.downloaded - 1)
                    name = _format_source_name(result.get("source", "desconhecido"))
                    if self.sources_count.get(name):
                        self.sources_count[name] -= 1
            else:
                self.failed = max(0, self.failed - 1)

    def update(self, result: dict) -> None:
        with self.lock:
            self._last_update_time = time.monotonic()
            self.current += 1
            success = result.get("success", False)
            skipped = result.get("skipped", False)
            source = result.get("source", "desconhecido")
            doi_or_title = result.get("doi") or result.get("searched_title") or "?"
            file_path = result.get("file")
            filename = Path(file_path).name if file_path else ""

            # Format Item Identifier
            if len(doi_or_title) > 36:
                short_id = doi_or_title[:33] + "..."
            else:
                short_id = doi_or_title.ljust(36)

            self.active_items.discard(doi_or_title[:35] + "..." if len(doi_or_title) > 38 else doi_or_title)

            # Update stats
            if success:
                if skipped:
                    self.cached += 1
                    status_badge = f"{Style.B_YELLOW}⚡ [{_format_source_name(source):^12}]{Style.RESET}"
                else:
                    self.downloaded += 1
                    self.sources_count[_format_source_name(source)] += 1
                    status_badge = f"{Style.B_GREEN}✅ [{_format_source_name(source):^12}]{Style.RESET}"
            else:
                self.failed += 1
                status_badge = f"{Style.B_RED}❌ [{'NÃO ENCONTRADO':^12}]{Style.RESET}"

            # Format Filename or Error
            if filename:
                if len(filename) > 38:
                    short_fname = filename[:35] + "..."
                else:
                    short_fname = filename
                target_str = f"{Style.GRAY}→{Style.RESET} {Style.WHITE}{short_fname}{Style.RESET}"
            else:
                target_str = f"{Style.DIM}(Esgotado em todas as bases){Style.RESET}"

            # 1. Print completed item line
            item_num = f"{Style.DIM}[{self.current:03d}/{self.total:03d}]{Style.RESET}"
            print(f"{item_num} {status_badge} {Style.CYAN}{short_id}{Style.RESET} {target_str}", flush=True)

            # 2. Render Live Progress Bar
            self._render_bar()

    def _render_bar(self) -> None:
        pct = (self.current / self.total) * 100.0
        bar_width = 24
        filled = int(bar_width * (self.current / self.total))
        remainder = (bar_width * (self.current / self.total)) - filled
        part_blocks = ["", "▏", "▎", "▍", "▌", "▋", "▊", "▉"]
        part_char = part_blocks[int(remainder * 8)] if filled < bar_width else ""
        empty = bar_width - filled - (1 if part_char else 0)
        bar_str = f"{Style.B_CYAN}{'█' * filled}{part_char}{Style.GRAY}{'░' * max(0, empty)}{Style.RESET}"

        elapsed = time.monotonic() - self.start_time
        speed = self.current / elapsed if elapsed > 0 else 0

        bar_line = (
            f"   {Style.BOLD}Progresso:{Style.RESET} [{bar_str}] "
            f"{Style.B_WHITE}{pct:5.1f}%{Style.RESET} ({self.current}/{self.total}) "
            f"│ {Style.GREEN}✅ {self.downloaded}{Style.RESET} "
            f"│ {Style.YELLOW}⚡ {self.cached}{Style.RESET} "
            f"│ {Style.RED}❌ {self.failed}{Style.RESET} "
            f"│ {Style.DIM}{speed:.1f} it/s{Style.RESET}"
        )
        print(f"{bar_line}\n", flush=True)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Cache & Index Management
# ---------------------------------------------------------------------------

import sqlite3

from identity import normalize_doi as _normalize_doi

_INDEX_LOCK = threading.Lock()
# Wall-clock budget per article; 0 disables it. Without it a single article
# can hold a worker for 10+ minutes walking every source and browser fallback.
ITEM_DEADLINE_S = float(os.environ.get("PAPER_FETCH_ITEM_DEADLINE", "90")) or None
_MIGRATED_DBS: set[str] = set()
# Index snapshot loaded once per run (see run_parallel_workers) so the cache
# pre-check does not reopen SQLite and reload every row for each item.
_INDEX_SNAPSHOT: dict[str, dict[str, dict]] = {}

def _get_db(out_dir: Path):
    db_path = out_dir / ".paper_fetch_index.db"
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    if str(db_path) in _MIGRATED_DBS:
        return conn
    conn.execute("CREATE TABLE IF NOT EXISTS downloads (doi TEXT PRIMARY KEY, filepath TEXT)")
    # Identity-tracking columns, added on top of the original two-column
    # table so existing indexes migrate in place instead of being discarded.
    # SQLite has no "ADD COLUMN IF NOT EXISTS"; duplicate-column errors from
    # a table that already migrated are expected and silently ignored.
    for ddl in (
        "ALTER TABLE downloads ADD COLUMN identity_validated INTEGER DEFAULT 0",
        "ALTER TABLE downloads ADD COLUMN validation_method TEXT",
        "ALTER TABLE downloads ADD COLUMN sha256 TEXT",
        "ALTER TABLE downloads ADD COLUMN validated_at TEXT",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already exists from a prior run
    _MIGRATED_DBS.add(str(db_path))
    return conn

def _load_download_index(out_dir: Path) -> dict[str, dict]:
    """Return {normalized_doi: {filepath, identity_validated, ...}}.

    Keyed by the fully normalized DOI (never a truncated slug or a raw,
    un-normalized string) so two different DOIs can never collide onto the
    same cache entry and the same DOI is always found regardless of the
    exact prefix/case it was written in.
    """
    with _INDEX_LOCK:
        try:
            conn = _get_db(out_dir)
            rows = conn.execute(
                "SELECT doi, filepath, identity_validated, validation_method, sha256, validated_at FROM downloads"
            ).fetchall()
            conn.close()
            return {
                _normalize_doi(row[0]): {
                    "filepath": row[1],
                    "identity_validated": bool(row[2]),
                    "validation_method": row[3],
                    "sha256": row[4],
                    "validated_at": row[5],
                }
                for row in rows
            }
        except Exception:
            return {}

def _record_download_index(
    out_dir: Path,
    doi: str,
    filepath: str,
    *,
    identity_validated: bool = False,
    validation_method: str | None = None,
    sha256: str | None = None,
    validated_at: str | None = None,
) -> None:
    """Persist a doi->file mapping, including the identity verdict that
    justified accepting it. A row is only ever treated as an instant cache
    hit by _check_already_downloaded() when identity_validated is true —
    see the docstring there.
    """
    key = _normalize_doi(doi)
    with _INDEX_LOCK:
        try:
            conn = _get_db(out_dir)
            conn.execute(
                "INSERT OR REPLACE INTO downloads "
                "(doi, filepath, identity_validated, validation_method, sha256, validated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, filepath, 1 if identity_validated else 0, validation_method, sha256, validated_at),
            )
            conn.close()
        except Exception as exc:
            print(f"[index] falha ao registrar {key}: {exc}", file=sys.stderr)


def _record_from_result(out_dir: Path, doi: str, result: dict) -> None:
    """Record a successful fetch() result into the index, carrying over the
    identity verdict fetch() already computed (see _identity_result_fields
    in fetch.py) instead of re-deriving or discarding it."""
    _record_download_index(
        out_dir,
        doi,
        result["file"],
        identity_validated=bool(result.get("identity_validated")),
        validation_method=result.get("validation_method"),
        sha256=result.get("sha256"),
        validated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def _is_valid_disk_pdf(p: Path) -> bool:
    try:
        if not p.is_file() or p.stat().st_size < 1024:
            return False
        with open(p, "rb") as f:
            header = f.read(10)
            if not header.startswith(b"%PDF"):
                return False
        data = p.read_bytes()
        valid, _, _ = validate_pdf_data(data)
        return valid
    except Exception:
        return False


def _check_already_downloaded(doi: str, out_dir: Path) -> Path | None:
    """Fast (no-PDF-parsing beyond validate_pdf_data) pre-check.

    Only ever returns a hit for an index row that was recorded with
    identity_validated=True — i.e. a file that fetch()'s own identity gate
    (see fetch._validate_downloaded_file) already confirmed belongs to this
    exact DOI. A cache entry from before this migration (identity_validated
    defaults to 0), or no entry at all, is deliberately NOT resolved here:
    returning None makes the caller fall through to fetch(), whose own
    pre-check re-validates (or quarantines) the file before it can ever be
    handed out as a match again. This keeps a single authoritative place
    for the identity decision instead of duplicating it in two files.
    """
    if not out_dir.exists():
        return None

    idx = _INDEX_SNAPSHOT.get(str(out_dir.resolve()))
    if idx is None:
        idx = _load_download_index(out_dir)
    entry = idx.get(_normalize_doi(doi))
    if entry and entry["identity_validated"]:
        p = out_dir / Path(entry["filepath"]).name
        if p.is_file() and _is_valid_disk_pdf(p):
            return p
    return None


# ---------------------------------------------------------------------------
# Parallel Worker Handlers with Connection Jitter
# ---------------------------------------------------------------------------

def _process_single_doi(
    doi: str,
    out_dir: Path,
    *,
    progress: TerminalProgressBar | None = None,
    sources: list[str] | None = None,
    overwrite: bool = False,
    timeout: int = 25,
) -> dict:
    if not overwrite:
        existing = _check_already_downloaded(doi, out_dir)
        if existing:
            # _check_already_downloaded only ever returns a hit for an index
            # row recorded with identity_validated=True (see its docstring).
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
                "validation_method": "cache_previously_validated",
            }

    if progress:
        progress.on_start(doi)

    # Small random jitter (0.05s - 0.25s) to avoid burst host collisions
    time.sleep(random.uniform(0.05, 0.25))

    fetch_module.set_item_deadline(ITEM_DEADLINE_S)
    try:
        res = fetch(
            doi,
            out_dir,
            dry_run=False,
            overwrite=overwrite,
            timeout=timeout,
            sources=sources,
        )
    finally:
        fetch_module.set_item_deadline(None)
    if res.get("success") and res.get("file"):
        _record_from_result(out_dir, doi, res)
    return res


def _process_single_title(
    title: str,
    out_dir: Path,
    *,
    progress: TerminalProgressBar | None = None,
    sources: list[str] | None = None,
    overwrite: bool = False,
    timeout: int = 25,
) -> dict:
    if progress:
        progress.on_start(title)

    time.sleep(random.uniform(0.05, 0.25))
    fetch_module.set_item_deadline(ITEM_DEADLINE_S)
    try:
        return _process_single_title_inner(title, out_dir, sources=sources, overwrite=overwrite, timeout=timeout)
    finally:
        fetch_module.set_item_deadline(None)


def _process_single_title_inner(
    title: str,
    out_dir: Path,
    *,
    sources: list[str] | None,
    overwrite: bool,
    timeout: int,
) -> dict:
    resolved_doi, title_resolution = _resolve_title(title, timeout=timeout)
    if not resolved_doi:
        # New direct-title recovery path: do NOT require a DOI to exist.
        # This is especially important for legacy proceedings, reports,
        # repository records, and articles whose DOI metadata is incomplete.
        try:
            direct_recovery = getattr(fetch_module, "fetch_title_direct", None)
            if callable(direct_recovery):
                recovery = direct_recovery(
                    title,
                    out_dir,
                    timeout=timeout,
                    overwrite=overwrite,
                    sources=sources,
                )
                if recovery.get("success"):
                    recovery["searched_title"] = title
                    recovery["title_resolution"] = title_resolution
                    if recovery.get("file"):
                        direct_doi = recovery.get("resolved_doi")
                        if direct_doi:
                            _record_from_result(out_dir, direct_doi, recovery)
                    return recovery
            # Se falhar nas bases acadêmicas, tenta nas patentes!
            try:
                import patent_fetch
                patent_result = patent_fetch.download_patent_by_title(title, out_dir, timeout=timeout)
                if patent_result.get("success"):
                    return {
                        "searched_title": title,
                        "resolved_doi": patent_result.get("patent_id"),
                        "success": True,
                        "source": "google_patents",
                        "pdf_url": "DuckDuckGo + Google Patents",
                        "file": patent_result.get("filepath"),
                        "meta": {"title": title, "patent_id": patent_result.get("patent_id")},
                        "sources_tried": title_resolution.get("resolvers_tried", []) + ["google_patents"],
                        "title_resolution": title_resolution,
                    }
            except Exception as pat_exc:
                pass

            return {
                "searched_title": title,
                "resolved_doi": None,
                "success": False,
                "source": "title_recovery",
                "pdf_url": None,
                "file": None,
                "meta": {"title": title},
                "sources_tried": title_resolution.get("resolvers_tried", []),
                "title_resolution": title_resolution,
                "error": {
                    "code": "title_resolve_failed",
                    "message": f"Could not resolve title to DOI or recover a PDF directly: {title!r}",
                    "retryable": True,
                },
            }
        except Exception as exc:
            return {
                "searched_title": title,
                "resolved_doi": None,
                "success": False,
                "source": "title_recovery",
                "pdf_url": None,
                "file": None,
                "meta": {"title": title},
                "sources_tried": title_resolution.get("resolvers_tried", []),
                "title_resolution": title_resolution,
                "error": {
                    "code": "title_recovery_error",
                    "message": str(exc),
                    "retryable": True,
                },
            }

    if not overwrite:
        existing = _check_already_downloaded(resolved_doi, out_dir)
        if existing:
            return {
                "searched_title": title,
                "resolved_doi": resolved_doi,
                "success": True,
                "source": "cache",
                "pdf_url": None,
                "file": str(existing),
                "meta": {"title": title},
                "sources_tried": [],
                "skipped": True,
                "skip_reason": "file_exists",
                "title_resolution": title_resolution,
                "identity_validated": True,
                "validation_method": "cache_previously_validated",
            }

    result = fetch(
        resolved_doi,
        out_dir,
        dry_run=False,
        overwrite=overwrite,
        timeout=timeout,
        sources=sources,
    )
    result["searched_title"] = title
    result["resolved_doi"] = resolved_doi
    result["title_resolution"] = title_resolution
    if result.get("success") and result.get("file"):
        _record_from_result(out_dir, resolved_doi, result)
    return result


# ---------------------------------------------------------------------------
# Main Parallel Orchestrator
# ---------------------------------------------------------------------------

def _safe_result_item_id(result: dict) -> str:
    return str(result.get("doi") or result.get("searched_title") or result.get("title") or "?")


def _throttled_hosts() -> dict:
    """Hosts still on cooldown when the run ended (they refused or failed)."""
    try:
        import http_retry

        return http_retry.snapshot()
    except Exception:
        return {}


def _write_bibtex(out_dir: Path) -> None:
    """Regenerate bibliografia.bib from the index (no duplicates across runs)."""
    entries = []
    for key, row in sorted(_load_download_index(out_dir).items()):
        if not row.get("identity_validated"):
            continue
        entries.append(f"@article{{{key.replace('/', '_')},\n  doi = {{{key}}},\n  file = {{{row['filepath']}}}\n}}\n")
    try:
        (out_dir / "bibliografia.bib").write_text("\n".join(entries), encoding="utf-8")
    except OSError as exc:
        print(f"[bib] falha ao gravar bibliografia.bib: {exc}", file=sys.stderr)


_EVENTS = os.environ.get("PAPER_FETCH_EVENTS", "").strip() in ("1", "true", "yes")
_EVENT_LOCK = threading.Lock()


def _emit_event(event: dict) -> None:
    """Structured progress for the web UI (backend forwards ``@@EVENT`` lines)."""
    if not _EVENTS:
        return
    with _EVENT_LOCK:
        print("@@EVENT " + json.dumps(event, ensure_ascii=False), flush=True)


# Failures that say "not now" rather than "no". A refusal (401/403/404) and a
# rejected identity are answers: asking again only spends time.
_SECOND_PASS_CODES = frozenset({
    "download_host_cooldown",
    "download_network_error",
    "download_timeout",
    "download_http_408",
    "download_http_425",
    "download_http_429",
    "worker_exception",
    "item_deadline",
    "download_item_deadline",
})


def _deserves_second_pass(result: dict) -> bool:
    """True when this item failed for a reason a later attempt could fix."""
    if result.get("success"):
        return False
    error = result.get("error")
    if not isinstance(error, dict):
        return False
    code = str(error.get("code") or "")
    if code in _SECOND_PASS_CODES:
        return True
    # Any 5xx: the server had a bad moment, the article is presumably there.
    return code.startswith("download_http_5")


def _item_status(result: dict) -> str:
    if not result.get("success"):
        return "failed"
    if result.get("skipped") or result.get("source") == "cache":
        return "cached"
    return "ok"


def _timed(fn, *args, **kwargs) -> dict:
    t0 = time.monotonic()
    result = fn(*args, **kwargs)
    result["_elapsed"] = round(time.monotonic() - t0, 2)
    return result




def run_parallel_workers(
    dois: list[str],
    titles: list[str],
    out_dir: Path,
    *,
    max_workers: int = 4,
    sources: list[str] | None = None,
    overwrite: bool = False,
    timeout: int = 25,
) -> tuple[list[dict], list[dict]]:
    total_items = len(dois) + len(titles)
    start_time = time.monotonic()

    banner_lines = [
        f"{Style.B_WHITE}🚀 DOWNLOAD PARALELO DE ARTIGOS CIENTÍFICOS (EXPANDIDO){Style.RESET}",
        f"{Style.DIM}📦 Total de itens:{Style.RESET} {Style.BOLD}{total_items}{Style.RESET} ({len(dois)} DOIs | {len(titles)} Títulos)",
        f"{Style.DIM}🔎 Fontes adicionais:{Style.RESET} OpenAIRE • HAL • Zenodo • DataCite • DOAJ",
        f"{Style.DIM}⚡ Sessões paralelas:{Style.RESET} {Style.BOLD}{max_workers} workers{Style.RESET}",
        f"{Style.DIM}📂 Destino:{Style.RESET} {Style.CYAN}{str(out_dir.resolve())[:46]}{Style.RESET}",
    ]
    print("\n" + render_box(banner_lines, border_color=Style.B_CYAN, inner_width=66) + "\n")

    progress = TerminalProgressBar(total_items)
    _INDEX_SNAPSHOT[str(out_dir.resolve())] = _load_download_index(out_dir)
    _emit_event({"type": "start", "total": total_items, "workers": max_workers})
    counts = Counter()
    by_source: Counter[str] = Counter()
    # Per-source telemetry across the run: attempts, hits, time and the
    # last error each source reported (feeds the dashboard).
    source_stats: dict[str, dict] = {}

    # Stable index is the central fix: completion order is irrelevant.
    indexed_items: list[tuple[int, str, str]] = []
    idx = 0
    for doi in dois:
        indexed_items.append((idx, "doi", doi))
        idx += 1
    for title in titles:
        indexed_items.append((idx, "title", title))
        idx += 1

    results_by_index: dict[int, dict] = {}

    def _record_outcome(result: dict, item_value: str) -> None:
        """Fold one finished item into the run totals and emit its events."""
        status = _item_status(result)
        counts[status] += 1
        for src_name, entry in (result.get("source_stats") or {}).items():
            agg = source_stats.setdefault(
                src_name, {"attempts": 0, "ok": 0, "failed": 0, "ms": 0.0, "last_error": None}
            )
            agg["attempts"] += entry.get("attempts", 0)
            agg["ok"] += entry.get("ok", 0)
            agg["failed"] += entry.get("failed", 0)
            agg["ms"] += entry.get("ms", 0.0)
            if entry.get("last_error"):
                agg["last_error"] = entry["last_error"]
        if status != "failed":
            by_source[_format_source_name(result.get("source"))] += 1
        err = result.get("error")
        _emit_event({
            "type": "item",
            "id": item_value,
            "status": status,
            "source": result.get("source"),
            "file": Path(result["file"]).name if result.get("file") else None,
            "seconds": result.get("_elapsed"),
            "error": (err.get("message") if isinstance(err, dict) else err) if status == "failed" else None,
        })
        _emit_event({
            "type": "progress", "done": sum(counts.values()), "total": total_items,
            "ok": counts["ok"], "cached": counts["cached"], "failed": counts["failed"],
            "elapsed": round(time.monotonic() - start_time, 1),
        })

    def _unrecord_outcome(result: dict) -> None:
        """Take a superseded verdict back out of the totals.

        Only the headline counters are reversed. The per-source attempt stats
        are deliberately left alone: the first attempt really did happen, and
        the point of those numbers is to show what each source costs.
        """
        status = _item_status(result)
        if counts.get(status):
            counts[status] -= 1
        if status != "failed":
            name = _format_source_name(result.get("source"))
            if by_source.get(name):
                by_source[name] -= 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_job: dict[concurrent.futures.Future, tuple[int, str, str]] = {}
        for input_index, item_type, item_value in indexed_items:
            if item_type == "doi":
                future = executor.submit(
                    _timed, _process_single_doi,
                    item_value,
                    out_dir,
                    progress=progress,
                    sources=sources,
                    overwrite=overwrite,
                    timeout=timeout,
                )
            else:
                future = executor.submit(
                    _timed, _process_single_title,
                    item_value,
                    out_dir,
                    progress=progress,
                    sources=sources,
                    overwrite=overwrite,
                    timeout=timeout,
                )
            future_to_job[future] = (input_index, item_type, item_value)

        for future in concurrent.futures.as_completed(future_to_job):
            input_index, item_type, item_value = future_to_job[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    ("doi" if item_type == "doi" else "searched_title"): item_value,
                    "success": False,
                    "source": "error",
                    "error": {
                        "code": "worker_exception",
                        "message": str(exc),
                        "retryable": True,
                    },
                }
            result["input_index"] = input_index
            result["input_type"] = item_type
            result["input_value"] = item_value
            results_by_index[input_index] = result
            progress.update(result)
            _record_outcome(result, item_value)

    # -----------------------------------------------------------------------
    # Second pass: items dropped for a reason that has since gone away.
    #
    # While the run is going, a host that refuses or stalls is put on a
    # cooldown and every article still queued for it is skipped fast, so one
    # bad host cannot consume the whole run. Those articles were never really
    # tried. The cooldowns have expired by now, so they get one more turn —
    # sequentially and with the throttle table cleared, which is also gentler
    # on the hosts that were struggling.
    # -----------------------------------------------------------------------
    retry_jobs = [
        (index, result)
        for index, result in sorted(results_by_index.items())
        if _deserves_second_pass(result)
    ]
    if retry_jobs:
        try:
            import http_retry

            http_retry.reset()
        except Exception:
            pass
        print(
            f"\n{Style.DIM}↻ Segunda passada: {len(retry_jobs)} "
            f"{'item' if len(retry_jobs) == 1 else 'itens'} que falharam por "
            f"indisponibilidade temporária (host em espera, timeout, rede).{Style.RESET}"
        )
        _emit_event({"type": "second_pass_start", "total": len(retry_jobs)})
        recovered = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(2, max_workers))) as retry_pool:
            retry_futures = {}
            for index, previous in retry_jobs:
                item_type = previous.get("input_type", "doi")
                item_value = previous.get("input_value") or previous.get("doi") or ""
                worker = _process_single_doi if item_type == "doi" else _process_single_title
                retry_futures[retry_pool.submit(
                    _timed, worker, item_value, out_dir,
                    progress=progress, sources=sources, overwrite=overwrite, timeout=timeout,
                )] = (index, previous, item_type, item_value)

            for future in concurrent.futures.as_completed(retry_futures):
                index, previous, item_type, item_value = retry_futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    continue  # keep the original failure; the report already explains it
                if not result.get("success"):
                    continue  # no better than before, so the first verdict stands
                result["input_index"] = index
                result["input_type"] = item_type
                result["input_value"] = item_value
                result["second_pass"] = True
                _unrecord_outcome(previous)
                progress.retract(previous)
                results_by_index[index] = result
                progress.update(result)
                _record_outcome(result, item_value)
                recovered += 1
        _emit_event({"type": "second_pass_done", "total": len(retry_jobs), "recovered": recovered})
        print(
            f"{Style.DIM}↻ Segunda passada: {Style.RESET}"
            f"{Style.B_GREEN}{recovered}{Style.RESET}"
            f"{Style.DIM} de {len(retry_jobs)} recuperados.{Style.RESET}"
        )

    progress.stop()

    # Reconstruct results in ORIGINAL input order. No zip(as_completed())
    # mapping is ever used.
    ordered_results = [results_by_index[i] for i in range(total_items) if i in results_by_index]
    doi_results = [r for r in ordered_results if r.get("input_type") == "doi"]
    title_results = [r for r in ordered_results if r.get("input_type") == "title"]

    # Within each class, preserve original relative order.
    doi_results.sort(key=lambda r: r.get("input_index", 10**9))
    title_results.sort(key=lambda r: r.get("input_index", 10**9))

    report_dois_path = SCRIPT_DIR / "Relatório.txt"
    report_titles_path = SCRIPT_DIR / "Relatório_artigos_por_nome.txt"

    if doi_results:
        try:
            report_dois_path.write_text(
                generate_detailed_report(doi_results, [r.get("doi", "") for r in doi_results]),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"{Style.B_YELLOW}⚠️ Falha ao gerar Relatório.txt: {exc}{Style.RESET}")

    if title_results:
        try:
            ordered_titles = [r.get("searched_title") or r.get("title") or "" for r in title_results]
            _write_title_report(title_results, ordered_titles, report_titles_path)
        except Exception as exc:
            print(f"{Style.B_YELLOW}⚠️ Falha ao gerar relatório de títulos: {exc}{Style.RESET}")

    total_elapsed = time.monotonic() - start_time
    total_success = progress.downloaded + progress.cached
    manual_or_recoverable = sum(
        1
        for r in ordered_results
        if isinstance(r.get("expanded_discovery"), dict)
        and r.get("expanded_discovery").get("status") == "exhausted"
        and not r.get("success")
    )
    # The headline quality metric is identity-confirmed articles over DOIs
    # processed, never raw "a PDF landed on disk" — a downloaded-but-wrong
    # PDF is tracked separately and is NOT counted among successes.
    identity_confirmed = sum(1 for r in ordered_results if r.get("success") and r.get("identity_validated") is True)
    identity_rejected = sum(
        1 for r in ordered_results
        if not r.get("success") and (r.get("error") or {}).get("code") == "article_identity_not_confirmed"
    )

    run_summary = {
        "type": "summary", "total": total_items, "ok": counts["ok"], "cached": counts["cached"],
        "failed": counts["failed"], "by_source": dict(by_source),
        "sources": {
            name: {
                "attempts": data["attempts"],
                "ok": data["ok"],
                "failed": data["failed"],
                "success_rate": round(data["ok"] / data["attempts"], 3) if data["attempts"] else 0.0,
                "avg_seconds": round(data["ms"] / data["attempts"] / 1000, 2) if data["attempts"] else 0.0,
                "last_error": data["last_error"],
            }
            for name, data in sorted(source_stats.items(), key=lambda kv: -kv[1]["attempts"])
        },
        "throttled_hosts": _throttled_hosts(),
        "elapsed": round(total_elapsed, 1),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _emit_event(run_summary)
    try:
        (out_dir / ".last_run.json").write_text(json.dumps(run_summary, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    _write_bibtex(out_dir)

    summary_lines = [
        f"{Style.B_WHITE}                 📊 RESUMO FINAL DA EXECUÇÃO{Style.RESET}",
        "---SEP---",
        f"{Style.BOLD}Total Processado:{Style.RESET}        {total_items} itens",
        f"{Style.B_GREEN}✅ Sucessos:{Style.RESET}               {total_success} artigos ({progress.downloaded} novos + {progress.cached} no cache)",
        f"{Style.B_CYAN}🔎 Identidade confirmada:{Style.RESET}   {identity_confirmed}/{total_items} ({(identity_confirmed / total_items * 100) if total_items else 0:.1f}%)",
    ]
    if identity_rejected:
        summary_lines.append(f"{Style.B_YELLOW}⚠️  Identidade rejeitada:{Style.RESET}    {identity_rejected} (PDF baixado não era o artigo solicitado)")
    for src_name, count in progress.sources_count.most_common():
        summary_lines.append(f"   • {src_name}: {count}")
    summary_lines.append(f"{Style.B_RED if progress.failed else Style.DIM}❌ Falhas finais:{Style.RESET}          {progress.failed} artigos")
    summary_lines.append(f"{Style.B_YELLOW}🔎 Falhas com discovery expandido:{Style.RESET} {manual_or_recoverable}")
    summary_lines.append(f"{Style.BOLD}⏱️  Tempo Total:{Style.RESET}            {_format_duration(total_elapsed)}")
    summary_lines.append("---SEP---")
    if doi_results:
        summary_lines.append(f"{Style.GREEN}📄 Relatório DOIs:{Style.RESET}       Relatório.txt")
    if title_results:
        summary_lines.append(f"{Style.GREEN}📄 Relatório Títulos:{Style.RESET}    Relatório_artigos_por_nome.txt")

    
    print("\n" + render_box(summary_lines, border_color=Style.B_MAGENTA, inner_width=66) + "\n")
    
    if progress.failed > 0:
        print(f"{Style.B_YELLOW}Há {progress.failed} itens não encontrados. Deseja realizar uma Busca Profunda Complementar (PubMed & NTRS)? [s/N]{Style.RESET}")
        import sys
        import select
        if sys.stdin.isatty():
            try:
                # timeout for input
                i, o, e = select.select([sys.stdin], [], [], 30)
                if i:
                    ans = sys.stdin.readline().strip().lower()
                    if ans == 's':
                        import subprocess
                        subprocess.run([sys.executable, str(SCRIPT_DIR / "deep_search.py")])
            except Exception:
                pass
        else:
            print(f"{Style.DIM}(Modo não-interativo detectado. Para iniciar, use o botão na interface Web ou rode src/deep_search.py manualmente.){Style.RESET}\n")

    return doi_results, title_results

# ===========================================================================
# END ADDITIVE ORDER-SAFE ORCHESTRATOR
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Multi-session parallel paper fetcher with clean terminal dashboard."
    )
    parser.add_argument(
        "--file",
        "-f",
        default="DOI's.txt",
        help="Input file containing DOIs and/or article titles (default: DOI's.txt)",
    )
    parser.add_argument(
        "--out",
        "-o",
        default="pdfs",
        help="Output directory for downloaded PDFs (default: pdfs)",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=8,
        help="Number of concurrent worker sessions (default: 8)",
    )
    parser.add_argument(
        "--sources",
        "-s",
        default=None,
        help="Comma-separated database sources to query (default: all sources)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-download even if the PDF already exists in the output directory",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Timeout in seconds per HTTP request (default: 15)",
    )

    args = parser.parse_args()

    input_file = Path(args.file)
    if not input_file.is_file():
        candidate = SCRIPT_DIR / args.file
        if candidate.is_file():
            input_file = candidate
        else:
            print(f"{Style.B_RED}❌ Erro:{Style.RESET} Arquivo de entrada não encontrado: {args.file}")
            sys.exit(1)

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = SCRIPT_DIR / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    sources_list = [s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None

    dois, titles = _load_dois_and_titles_from_file(input_file)
    dois = list(dict.fromkeys(dois))
    titles = list(dict.fromkeys(titles))
    if not dois and not titles:
        print(f"{Style.B_YELLOW}⚠️ Aviso:{Style.RESET} Nenhum DOI ou título válido encontrado em {input_file}")
        sys.exit(0)

    run_parallel_workers(
        dois,
        titles,
        out_dir,
        max_workers=args.workers,
        sources=sources_list,
        overwrite=args.overwrite,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
