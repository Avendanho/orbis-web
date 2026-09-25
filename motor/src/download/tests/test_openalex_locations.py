"""OpenAlex location handling: landing pages are candidates too.

Measured on this project's own backlog: of the open-access articles that the
run failed to retrieve, 81 of 137 had no ``pdf_url`` in OpenAlex at all —
only a landing page. Roughly a third of those pages publish the file in a
``citation_pdf_url`` meta tag, which the downloader already knows how to
follow. Dropping the landing pages threw that away before it could be tried.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch


def work(*locations: dict) -> dict:
    return {
        "results": [{
            "title": "A paper",
            "publication_year": 2020,
            "authorships": [{"author": {"display_name": "Ana Souza"}}],
            "primary_location": {"source": {"display_name": "J. Tests"}},
            "best_oa_location": locations[0] if locations else {},
            "locations": list(locations),
        }]
    }


class LocationHarvestTests(unittest.TestCase):
    def test_pdf_urls_come_before_landing_pages(self):
        payload = work(
            {"is_oa": True, "pdf_url": None, "landing_page_url": "https://repo.example/item/1"},
            {"is_oa": True, "pdf_url": "https://repo.example/file.pdf", "landing_page_url": None},
        )
        with patch.object(fetch, "_get_json", return_value=payload):
            urls, _meta = fetch.try_openalex("10.1/x", timeout=5)
        self.assertEqual(urls[0], "https://repo.example/file.pdf")
        self.assertIn("https://repo.example/item/1", urls)

    def test_landing_page_is_offered_when_no_pdf_url_exists(self):
        payload = work({"is_oa": True, "pdf_url": None,
                        "landing_page_url": "https://pmc.example/articles/PMC1/"})
        with patch.object(fetch, "_get_json", return_value=payload):
            urls, _meta = fetch.try_openalex("10.1/x", timeout=5)
        self.assertEqual(urls, ["https://pmc.example/articles/PMC1/"])

    def test_closed_locations_are_ignored(self):
        payload = work({"is_oa": False, "pdf_url": "https://paywall.example/f.pdf",
                        "landing_page_url": "https://paywall.example/item"})
        with patch.object(fetch, "_get_json", return_value=payload):
            urls, _meta = fetch.try_openalex("10.1/x", timeout=5)
        self.assertEqual(urls, [])

    def test_a_landing_page_is_never_listed_twice(self):
        same = "https://repo.example/item/1"
        payload = work(
            {"is_oa": True, "pdf_url": None, "landing_page_url": same},
            {"is_oa": True, "pdf_url": None, "landing_page_url": same},
        )
        with patch.object(fetch, "_get_json", return_value=payload):
            urls, _meta = fetch.try_openalex("10.1/x", timeout=5)
        self.assertEqual(urls, [same])

    def test_a_landing_page_identical_to_a_pdf_url_is_not_repeated(self):
        same = "https://repo.example/file.pdf"
        payload = work({"is_oa": True, "pdf_url": same, "landing_page_url": same})
        with patch.object(fetch, "_get_json", return_value=payload):
            urls, _meta = fetch.try_openalex("10.1/x", timeout=5)
        self.assertEqual(urls, [same])

    def test_metadata_is_still_returned(self):
        payload = work({"is_oa": True, "pdf_url": "https://repo.example/f.pdf"})
        with patch.object(fetch, "_get_json", return_value=payload):
            _urls, meta = fetch.try_openalex("10.1/x", timeout=5)
        self.assertEqual(meta["title"], "A paper")
        self.assertEqual(meta["author"], "Ana Souza")
        self.assertEqual(meta["journal"], "J. Tests")


if __name__ == "__main__":
    unittest.main()
