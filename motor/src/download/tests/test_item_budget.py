"""The per-article budget must hold: the ORBIS gives up on the motor at 90 s + 30 s."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bypass403
import fetch
import sources_shadow

DOI = "10.1016/j.jaac.2016.07.155"


def _run(sources, **patches):
    with tempfile.TemporaryDirectory() as d, \
            patch.dict("os.environ", {"PAPER_FETCH_TITLE_RECOVERY": "0"}), \
            patch.multiple(fetch, **patches):
        return fetch.fetch(DOI, Path(d), dry_run=False, overwrite=False, timeout=5, sources=sources)


class ItemBudgetTests(unittest.TestCase):
    def setUp(self):
        fetch._format = "silent"
        fetch._libgen_paused_until = 0.0

    def tearDown(self):
        fetch.set_item_deadline(None)

    def test_no_source_starts_once_the_budget_is_spent(self):
        crossref = MagicMock(return_value=([], {}))
        fetch.set_item_deadline(0.01)
        time.sleep(0.05)
        _run(["crossref"], try_crossref_links=crossref)
        crossref.assert_not_called()

    def test_no_source_starts_with_only_a_sliver_of_budget_left(self):
        crossref = MagicMock(return_value=([], {}))
        fetch.set_item_deadline(fetch.MIN_SOURCE_SECONDS / 2)
        _run(["crossref"], try_crossref_links=crossref)
        crossref.assert_not_called()

    def test_scihub_runs_before_the_slow_niche_fallbacks(self):
        order = []
        _run(
            ["scihub", "osti"],
            try_scihub=MagicMock(side_effect=lambda *a, **k: order.append("scihub")),
            try_osti=MagicMock(side_effect=lambda *a, **k: order.append("osti")),
            _is_scihub_enabled=MagicMock(return_value=True),
        )
        self.assertEqual(order, ["scihub", "osti"])

    def test_annas_archive_stops_between_requests_when_the_budget_runs_out(self):
        def expire(*a, **k):
            fetch.set_item_deadline(0.001)
            time.sleep(0.01)
            return bypass403.BypassResult(False, 0, error="timeout")

        fetch.set_item_deadline(60)
        with patch.object(sources_shadow, "bypass_get", side_effect=expire) as get:
            self.assertIsNone(sources_shadow.try_annas_archive(DOI, timeout=10))
        self.assertEqual(get.call_count, 1)


class LibgenRateLimitTests(unittest.TestCase):
    def setUp(self):
        fetch._format = "silent"
        fetch._libgen_paused_until = 0.0

    def tearDown(self):
        fetch._libgen_paused_until = 0.0

    def test_rate_limit_answer_pauses_libgen_for_the_next_articles(self):
        libgen = MagicMock(return_value=(["https://libgen.li/get.php?md5=abc&key=x"], {}))
        download = MagicMock(return_value="http_500")
        _run(["libgen"], try_libgen=libgen, _download=download)
        _run(["libgen"], try_libgen=libgen, _download=download)
        self.assertEqual(libgen.call_count, 1)

    def test_other_errors_do_not_pause_libgen(self):
        libgen = MagicMock(return_value=(["https://libgen.li/get.php?md5=abc&key=x"], {}))
        download = MagicMock(return_value="timeout")
        _run(["libgen"], try_libgen=libgen, _download=download)
        _run(["libgen"], try_libgen=libgen, _download=download)
        self.assertEqual(libgen.call_count, 2)


class SlowBody:
    """A server that trickles the file: every read waits, as libgen does at ~16 KB/s."""

    def __init__(self, chunks=50, delay=0.2):
        self.left, self.delay = chunks, delay

    def geturl(self):
        return "https://libgen.li/get.php?md5=x"

    def _one(self):
        if self.left <= 0:
            return b""
        self.left -= 1
        time.sleep(self.delay)
        return b"%PDF-1.4 " + b"x" * 1000

    def read(self, amt=None):
        if amt is not None and amt <= 1 << 16:
            return self._one()
        data = b""
        while (chunk := self._one()):
            data += chunk
        return data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SlowTransferTests(unittest.TestCase):
    def setUp(self):
        fetch._format = "silent"

    def test_a_trickling_download_is_cut_at_its_timeout(self):
        with tempfile.TemporaryDirectory() as d, \
                patch.object(fetch, "_url_fetch_allowed", return_value=(True, "")), \
                patch.object(fetch, "bypass_download_pdf", return_value=(False, "timeout")), \
                patch.object(fetch.urllib.request, "urlopen", return_value=SlowBody()):
            started = time.monotonic()
            err = fetch._download("https://libgen.li/get.php?md5=x", Path(d) / "a.pdf", timeout=1)
            elapsed = time.monotonic() - started
        self.assertEqual(err, "timeout")
        self.assertLess(elapsed, 2.5)


if __name__ == "__main__":
    unittest.main()
