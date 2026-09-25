"""Tests for the retry policy, the per-host cooldown and the futile-link filter."""
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch
import http_retry
import institutional


class RetryPolicyTests(unittest.TestCase):
    def setUp(self):
        http_retry.reset()

    def tearDown(self):
        http_retry.reset()

    def test_only_transient_statuses_are_retryable(self):
        for status in (429, 500, 502, 503, 504, 408):
            self.assertIn(status, http_retry.RETRYABLE_STATUS)
        for status in (400, 401, 403, 404, 410):
            self.assertNotIn(status, http_retry.RETRYABLE_STATUS)

    def test_retry_after_is_parsed_in_both_formats(self):
        self.assertEqual(http_retry.parse_retry_after("120"), 120.0)
        self.assertIsNone(http_retry.parse_retry_after(None))
        self.assertIsNone(http_retry.parse_retry_after("qualquer coisa"))
        seconds = http_retry.parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT")
        self.assertIsNotNone(seconds)
        self.assertGreater(seconds, 0)

    def test_backoff_grows_and_is_jittered_within_bounds(self):
        first = [http_retry.backoff_delay(0) for _ in range(20)]
        third = [http_retry.backoff_delay(2) for _ in range(20)]
        self.assertTrue(all(http_retry.BASE_DELAY / 2 <= d <= http_retry.BASE_DELAY for d in first))
        self.assertGreater(sum(third) / len(third), sum(first) / len(first))
        self.assertTrue(all(d <= http_retry.MAX_DELAY for d in third))
        self.assertGreater(len(set(first)), 1)  # jitter, not a fixed delay

    def test_retry_after_overrides_the_backoff_but_is_capped(self):
        self.assertEqual(http_retry.backoff_delay(0, retry_after=5), 5)
        self.assertEqual(http_retry.backoff_delay(0, retry_after=99999), http_retry.MAX_RETRY_AFTER)

    def test_host_goes_on_cooldown_after_repeated_failures(self):
        url = "https://flaky.example.org/a.pdf"
        for _ in range(http_retry.COOLDOWN_AFTER_FAILURES - 1):
            self.assertEqual(http_retry.note_failure(url, status=500), 0.0)
            self.assertTrue(http_retry.host_ready(url))
        self.assertGreater(http_retry.note_failure(url, status=500), 0.0)
        self.assertFalse(http_retry.host_ready(url))
        # Other hosts are unaffected.
        self.assertTrue(http_retry.host_ready("https://healthy.example.org/b.pdf"))
        self.assertIn("flaky.example.org", http_retry.snapshot())

    def test_retry_after_puts_the_host_on_cooldown_immediately(self):
        url = "https://api.example.org/x"
        self.assertEqual(http_retry.note_failure(url, status=429, retry_after=30), 30)
        self.assertFalse(http_retry.host_ready(url))

    def test_success_clears_the_failure_streak(self):
        url = "https://api.example.org/x"
        http_retry.note_failure(url, status=500)
        http_retry.note_success(url)
        self.assertEqual(http_retry.snapshot(), {})


class MetadataRetryTests(unittest.TestCase):
    def setUp(self):
        fetch._format = "silent"
        http_retry.reset()
        fetch.set_item_deadline(None)

    def tearDown(self):
        http_retry.reset()

    def _response(self, payload=b"{}"):
        class R:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): pass
            def read(self_inner): return payload
        return R()

    def test_transient_failure_is_retried_then_succeeds(self):
        error = urllib.error.HTTPError("u", 503, "busy", {}, None)
        calls = [error, self._response(b'{"ok": true}')]
        with patch.object(fetch.urllib.request, "urlopen", side_effect=calls), \
             patch.object(http_retry, "backoff_delay", return_value=0):
            self.assertEqual(fetch._get("https://api.example.org/x", timeout=5), b'{"ok": true}')

    def test_refusals_are_not_retried(self):
        error = urllib.error.HTTPError("u", 403, "no", {}, None)
        with patch.object(fetch.urllib.request, "urlopen", side_effect=error) as urlopen:
            with self.assertRaises(urllib.error.HTTPError):
                fetch._get("https://api.example.org/x", timeout=5)
        self.assertEqual(urlopen.call_count, 1)

    def test_host_on_cooldown_is_not_contacted_again(self):
        url = "https://api.example.org/x"
        http_retry.note_failure(url, status=503, retry_after=60)
        with patch.object(fetch.urllib.request, "urlopen") as urlopen:
            with self.assertRaises(TimeoutError):
                fetch._get(url, timeout=5)
            urlopen.assert_not_called()

    def test_download_skips_a_host_on_cooldown(self):
        url = "https://flaky.example.org/a.pdf"
        http_retry.note_failure(url, status=500, retry_after=60)
        # The SSRF gate runs first and would reject this made-up host.
        with patch.object(fetch, "bypass_download_pdf") as transport, \
             patch.object(fetch, "_url_fetch_allowed", return_value=(True, "")):
            result = fetch._download(url, Path("/tmp/none.pdf"), timeout=5)
        self.assertEqual(result, "host_cooldown")
        transport.assert_not_called()

    def test_transient_download_errors_feed_the_throttle(self):
        url = "https://flaky.example.org/a.pdf"
        for _ in range(http_retry.COOLDOWN_AFTER_FAILURES):
            fetch._note_transient(url, "http_500")
        self.assertFalse(http_retry.host_ready(url))
        http_retry.reset()
        fetch._note_transient(url, "http_404")  # a real answer: not throttled
        self.assertTrue(http_retry.host_ready(url))


class FutileLinkTests(unittest.TestCase):
    def test_tdm_links_are_dropped_without_the_credential_they_need(self):
        with patch.dict(os.environ, {"WILEY_TDM_TOKEN": ""}):
            self.assertTrue(fetch._crossref_link_is_futile(
                "https://api.wiley.com/onlinelibrary/tdm/v1/articles/10.1111%2Fx", "application/pdf"))
        with patch.dict(os.environ, {"WILEY_TDM_TOKEN": "token"}):
            self.assertFalse(fetch._crossref_link_is_futile(
                "https://api.wiley.com/onlinelibrary/tdm/v1/articles/10.1111%2Fx", "application/pdf"))

    def test_non_pdf_content_types_are_dropped(self):
        self.assertTrue(fetch._crossref_link_is_futile(
            "https://api.elsevier.com/content/article/PII:S1?httpAccept=text/plain", "text/plain"))

    def test_ordinary_publisher_pdfs_are_kept(self):
        self.assertFalse(fetch._crossref_link_is_futile("https://publisher.org/a.pdf", "application/pdf"))


class ProxyListTests(unittest.TestCase):
    def setUp(self):
        for var in ("PAPER_FETCH_PROXY", "PROXY_URL", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            os.environ.pop(var, None)

    def test_several_proxies_are_read_in_order(self):
        with patch.dict(os.environ, {"PAPER_FETCH_PROXY": "http://campus:3128, socks5://vpn:1080"}):
            self.assertEqual(institutional.proxy_urls(), ["http://campus:3128", "socks5://vpn:1080"])
            self.assertEqual(institutional.proxy_url(), "http://campus:3128")

    def test_proxy_label_never_leaks_credentials(self):
        self.assertEqual(institutional._proxy_label("socks5://user:secret@vpn.puc.br:1080"), "vpn.puc.br")

    def test_no_proxy_configured_is_still_a_no_op(self):
        self.assertEqual(institutional.proxy_urls(), [])
        self.assertFalse(institutional.is_configured())


if __name__ == "__main__":
    unittest.main()
