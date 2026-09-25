"""Offline tests for the PMC Open Access S3 route.

The bucket is the only PMC surface that still serves full text to a plain
HTTP client: the article pages answer a reCAPTCHA interstitial and the old
``oa.fcgi`` service was retired in the August 2026 migration. These tests
pin the two things that are easy to get wrong when reading a bucket listing:
picking the newest version of an article, and never mistaking a supplementary
file for the article itself.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pmc_s3


def listing(*keys: str) -> bytes:
    body = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<IsTruncated>false</IsTruncated>" + body + "</ListBucketResult>"
    ).encode()


class AssetSelectionTests(unittest.TestCase):
    def test_picks_canonical_pdf_and_xml(self):
        with patch.object(pmc_s3, "_get", return_value=listing(
            "PMC10350077.1/PMC10350077.1.json",
            "PMC10350077.1/PMC10350077.1.pdf",
            "PMC10350077.1/PMC10350077.1.txt",
            "PMC10350077.1/PMC10350077.1.xml",
        )):
            assets = pmc_s3.list_assets("PMC10350077", timeout=5)
        self.assertEqual(assets["version"], 1)
        self.assertTrue(assets["pdf"].endswith("/PMC10350077.1/PMC10350077.1.pdf"))
        self.assertTrue(assets["xml"].endswith("/PMC10350077.1/PMC10350077.1.xml"))

    def test_supplementary_pdf_is_never_mistaken_for_the_article(self):
        """A MOESM/ESM supplement is a different document with the same suffix."""
        with patch.object(pmc_s3, "_get", return_value=listing(
            "PMC10422071.1/MGG3-11-e2191-g001.jpg",
            "PMC10422071.1/MGG3-11-e2191-s001.pdf",
            "PMC10422071.1/41390_2023_2527_MOESM1_ESM.pdf",
            "PMC10422071.1/PMC10422071.1.xml",
        )):
            assets = pmc_s3.list_assets("PMC10422071", timeout=5)
        self.assertIsNone(assets["pdf"])
        self.assertIsNotNone(assets["xml"])

    def test_newest_version_wins(self):
        with patch.object(pmc_s3, "_get", return_value=listing(
            "PMC10000338.1/PMC10000338.1.pdf",
            "PMC10000338.1/PMC10000338.1.xml",
            "PMC10000338.2/PMC10000338.2.pdf",
            "PMC10000338.2/PMC10000338.2.xml",
        )):
            assets = pmc_s3.list_assets("PMC10000338", timeout=5)
        self.assertEqual(assets["version"], 2)
        self.assertIn("PMC10000338.2/PMC10000338.2.pdf", assets["pdf"])

    def test_article_absent_from_the_open_access_bucket(self):
        with patch.object(pmc_s3, "_get", return_value=listing()):
            assets = pmc_s3.list_assets("PMC99999999", timeout=5)
        self.assertIsNone(assets["pdf"])
        self.assertIsNone(assets["xml"])
        self.assertIsNone(assets["version"])

    def test_prefix_is_anchored_so_a_shorter_id_cannot_match_a_longer_one(self):
        """PMC1000 must not list PMC10000338: the trailing dot anchors it."""
        seen = {}

        def capture(url, *, timeout):
            seen["url"] = url
            return listing()

        with patch.object(pmc_s3, "_get", side_effect=capture):
            pmc_s3.list_assets("PMC1000", timeout=5)
        self.assertIn("prefix=PMC1000.", seen["url"])

    def test_accepts_a_bare_numeric_id(self):
        seen = {}

        def capture(url, *, timeout):
            seen["url"] = url
            return listing()

        with patch.object(pmc_s3, "_get", side_effect=capture):
            pmc_s3.list_assets("10350077", timeout=5)
        self.assertIn("prefix=PMC10350077.", seen["url"])

    def test_transport_failure_is_a_miss_not_a_crash(self):
        with patch.object(pmc_s3, "_get", side_effect=OSError("connection reset")):
            assets = pmc_s3.list_assets("PMC10350077", timeout=5)
        self.assertIsNone(assets["pdf"])
        self.assertIsNone(assets["xml"])


class TruncatedListingTests(unittest.TestCase):
    def test_follows_the_continuation_token(self):
        pages = [
            (
                '<?xml version="1.0"?><ListBucketResult>'
                "<IsTruncated>true</IsTruncated>"
                "<NextContinuationToken>tok1</NextContinuationToken>"
                "<Contents><Key>PMC1.1/fig1.jpg</Key></Contents>"
                "</ListBucketResult>"
            ).encode(),
            listing("PMC1.1/PMC1.1.pdf"),
        ]
        calls = []

        def paged(url, *, timeout):
            calls.append(url)
            return pages[len(calls) - 1]

        with patch.object(pmc_s3, "_get", side_effect=paged):
            assets = pmc_s3.list_assets("PMC1", timeout=5)
        self.assertEqual(len(calls), 2)
        self.assertIn("continuation-token=tok1", calls[1])
        self.assertIsNotNone(assets["pdf"])


if __name__ == "__main__":
    unittest.main()
