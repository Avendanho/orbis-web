"""Sci-Hub's file host answers 403 unless the request comes from a mirror page."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bypass403
import fetch

PDF_URL = "https://sci.bban.top/pdf/10.1016/j.jaac.2016.07.155.pdf"


class DownloadRefererTests(unittest.TestCase):
    def setUp(self):
        fetch._format = "silent"

    def test_referer_reaches_both_transports(self):
        seen = []

        def urlopen(req, timeout=None):
            seen.append(req.get_header("Referer"))
            raise fetch.urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

        with tempfile.TemporaryDirectory() as d, \
                patch.object(fetch, "_url_fetch_allowed", return_value=(True, "")), \
                patch.object(fetch, "bypass_download_pdf", return_value=(False, "http_403")) as transport, \
                patch.object(fetch.urllib.request, "urlopen", side_effect=urlopen):
            fetch._download(PDF_URL, Path(d) / "a.pdf", timeout=5, referer="https://sci-hub.mk/")
        self.assertEqual(transport.call_args.kwargs.get("headers"), {"Referer": "https://sci-hub.mk/"})
        self.assertEqual(seen, ["https://sci-hub.mk/"])

    def test_without_referer_the_host_itself_is_used(self):
        seen = []

        def urlopen(req, timeout=None):
            seen.append(req.get_header("Referer"))
            raise fetch.urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        with tempfile.TemporaryDirectory() as d, \
                patch.object(fetch, "_url_fetch_allowed", return_value=(True, "")), \
                patch.object(fetch, "bypass_download_pdf", return_value=(False, "http_404")) as transport, \
                patch.object(fetch.urllib.request, "urlopen", side_effect=urlopen):
            fetch._download("https://example.org/a.pdf", Path(d) / "a.pdf", timeout=5)
        self.assertIsNone(transport.call_args.kwargs.get("headers"))
        self.assertEqual(seen, ["https://example.org/"])

    def test_engine_download_forwards_headers(self):
        engine = bypass403.GoByPASS403Engine()
        with patch.object(engine, "execute_request", return_value=bypass403.BypassResult(False, 403, error="http_403")) as ex, \
                tempfile.TemporaryDirectory() as d:
            engine.download_pdf(PDF_URL, Path(d) / "a.pdf", timeout=5, headers={"Referer": "https://sci-hub.mk/"})
        self.assertEqual(ex.call_args.kwargs.get("custom_headers"), {"Referer": "https://sci-hub.mk/"})

    def test_scihub_candidate_is_fetched_with_its_mirror_as_referer(self):
        download = MagicMock(return_value="http_403")
        with tempfile.TemporaryDirectory() as d, \
                patch.object(fetch, "try_scihub", return_value=(PDF_URL, "sci-hub.mk")), \
                patch.object(fetch, "_is_scihub_enabled", return_value=True), \
                patch.object(fetch, "_download", download), \
                patch.dict("os.environ", {"PAPER_FETCH_TITLE_RECOVERY": "0"}):
            fetch.fetch("10.1016/j.jaac.2016.07.155", Path(d), dry_run=False, overwrite=False, timeout=5, sources=["scihub"])
        calls = [c for c in download.call_args_list if c.args[0] == PDF_URL]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs.get("referer"), "https://sci-hub.mk/")


if __name__ == "__main__":
    unittest.main()
