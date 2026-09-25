"""Offline tests for the PMC bucket, Europe PMC links, OpenAlex content, Wayback and API-key sources."""
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch

S3_LISTING = b"""<?xml version="1.0" encoding="UTF-8"?><ListBucketResult>
<Contents><Key>PMC123.1/PMC123.1.json</Key></Contents>
<Contents><Key>PMC123.1/PMC123.1.pdf</Key></Contents>
<Contents><Key>PMC123.2/PMC123.2.pdf</Key></Contents>
<Contents><Key>PMC123.2/PMC123.2.xml</Key></Contents>
</ListBucketResult>"""


class NewSourceTests(unittest.TestCase):
    def setUp(self):
        fetch._format = "silent"
        fetch._wayback_paused_until = 0.0
        fetch._wayback_next_at = 0.0

    def test_pmc_bucket_picks_latest_pdf_version(self):
        with patch.object(fetch, "_get", return_value=S3_LISTING) as get:
            url = fetch.try_pmc("123", timeout=5)
        self.assertEqual(url, "https://pmc-oa-opendata.s3.amazonaws.com/PMC123.2/PMC123.2.pdf")
        self.assertIn("prefix=PMC123.", get.call_args.args[0])

    def test_pmc_bucket_without_pdf_returns_none(self):
        with patch.object(fetch, "_get", return_value=b"<ListBucketResult></ListBucketResult>"):
            self.assertIsNone(fetch.try_pmc("PMC9", timeout=5))

    def test_europe_pmc_render_url(self):
        self.assertEqual(fetch.try_europe_pmc("123"), "https://europepmc.org/articles/PMC123?pdf=render")

    def test_idconv_skips_error_records(self):
        payload = {"records": [{"requested-id": "10.1/x", "status": "error"}]}
        with patch.object(fetch, "_get_json", return_value=payload):
            self.assertEqual(fetch.try_pmc_idconv("10.1/x", timeout=5), {})
        payload = {"records": [{"pmcid": "PMC77", "pmid": 55}]}
        with patch.object(fetch, "_get_json", return_value=payload):
            self.assertEqual(fetch.try_pmc_idconv("10.1/x", timeout=5), {"pmcid": "PMC77", "pmid": "55"})

    def test_pmcid_from_pubmed_efetch_articleid(self):
        xml = b'<PubmedArticleSet><PubmedArticle><PubmedData><ArticleIdList><ArticleId IdType="pubmed">1</ArticleId><ArticleId IdType="pmc">PMC4242</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>'
        with patch.object(fetch, "try_pmc_idconv", return_value={}), patch.object(fetch, "_get", return_value=xml):
            self.assertEqual(fetch.try_pmcid_from_pmid("1", timeout=5), "PMC4242")

    def test_europe_pmc_links_keeps_only_open_copies_pdf_first(self):
        payload = {"hitCount": 1, "resultList": {"result": [{
            "doi": "10.1/X", "pmcid": "PMC5",
            "fullTextUrlList": {"fullTextUrl": [
                {"availabilityCode": "S", "documentStyle": "pdf", "url": "https://paywall/x.pdf"},
                {"availabilityCode": "OA", "documentStyle": "html", "url": "https://pub/x.html"},
                {"availabilityCode": "OA", "documentStyle": "pdf", "url": "https://pub/x.pdf"},
                {"availabilityCode": "OA", "documentStyle": "pdf", "url": "https://europepmc.org/articles/PMC5?pdf=render"},
            ]},
        }]}}
        with patch.object(fetch, "_get_json", return_value=payload):
            urls, pmcid = fetch.try_europe_pmc_links("10.1/x", timeout=5)
        self.assertEqual(urls, ["https://pub/x.pdf", "https://pub/x.html"])
        self.assertEqual(pmcid, "PMC5")

    def test_europe_pmc_links_ignores_other_doi(self):
        payload = {"hitCount": 1, "resultList": {"result": [{"doi": "10.1/other", "fullTextUrlList": {"fullTextUrl": [
            {"availabilityCode": "OA", "documentStyle": "pdf", "url": "https://pub/other.pdf"}]}}]}}
        with patch.object(fetch, "_get_json", return_value=payload):
            self.assertEqual(fetch.try_europe_pmc_links("10.1/x", timeout=5), ([], None))

    def test_core_uses_fielded_doi_query(self):
        seen = {}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"results": []}'

        def urlopen(req, timeout):
            seen["url"] = req.full_url
            return Resp()

        with patch.object(fetch, "CORE_API_KEY", "k"), patch.object(fetch.urllib.request, "urlopen", urlopen):
            fetch.try_core("10.1/x", timeout=5)
        self.assertIn("q=doi%3A%2210.1%2Fx%22", seen["url"])

    def test_api_keys_attached_only_to_their_hosts(self):
        with patch.dict(os.environ, {"OPENALEX_API_KEY": "oak", "SEMANTIC_SCHOLAR_API_KEY": "s2k"}):
            h = {}
            self.assertEqual(fetch._with_api_auth("https://api.openalex.org/works?x=1", h), "https://api.openalex.org/works?x=1&api_key=oak")
            self.assertEqual(h, {})
            fetch._with_api_auth("https://api.semanticscholar.org/graph/v1/paper/DOI:1", h)
            self.assertEqual(h, {"x-api-key": "s2k"})
            h = {}
            self.assertEqual(fetch._with_api_auth("https://example.org/?q=1", h), "https://example.org/?q=1")
            self.assertEqual(h, {})

    def test_openalex_content_requires_key_and_pdf_flag(self):
        with patch.dict(os.environ, {"OPENALEX_API_KEY": ""}):
            self.assertIsNone(fetch.try_openalex_content("10.1/x", timeout=5))
        with patch.dict(os.environ, {"OPENALEX_API_KEY": "oak"}):
            with patch.object(fetch, "_get_json", return_value={"id": "https://openalex.org/W1", "has_content": {"pdf": False}}):
                self.assertIsNone(fetch.try_openalex_content("10.1/x", timeout=5))
            with patch.object(fetch, "_get_json", return_value={"id": "https://openalex.org/W1", "has_content": {"pdf": True}}):
                self.assertEqual(fetch.try_openalex_content("10.1/x", timeout=5), "https://content.openalex.org/works/W1.pdf?api_key=oak")

    def test_wayback_returns_raw_capture_of_newest_pdf(self):
        rows = [["timestamp", "original"], ["2019", "https://pub/x.pdf"], ["2024", "https://pub/x.pdf"]]
        with patch.object(fetch, "_get_json", return_value=rows) as gj:
            url = fetch.try_wayback("https://pub/x.pdf", timeout=5)
        self.assertEqual(url, "https://web.archive.org/web/2024id_/https://pub/x.pdf")
        self.assertIn("mimetype%3Aapplication%2Fpdf", gj.call_args.args[0])

    def test_wayback_pauses_after_429(self):
        err = urllib.error.HTTPError("u", 429, "Too Many", {}, None)
        with patch.object(fetch, "_get_json", side_effect=err):
            self.assertIsNone(fetch.try_wayback("https://pub/x.pdf", timeout=5))
        with patch.object(fetch, "_get_json") as gj:
            self.assertIsNone(fetch.try_wayback("https://pub/y.pdf", timeout=5))
            gj.assert_not_called()

    def test_mdpi_suffix_decoding(self):
        self.assertEqual(fetch._mdpi_volume_article_guesses("9030084"), [(9, 84)])
        self.assertEqual(fetch._mdpi_volume_article_guesses("242015170"), [(242, 5170), (24, 15170)])

    def test_mdpi_prefers_crossref_volume_and_article(self):
        msg = {"message": {"volume": "24", "article-number": "15170", "container-title": ["IJMS"]}}
        with patch.object(fetch, "_get_json", return_value=msg):
            urls = fetch._mdpi_pdf_candidates("10.3390/ijms242015170")
        self.assertEqual(urls[0], "https://pub.mdpi-res.com/ijms/ijms-24-15170/article_deploy/ijms-24-15170.pdf")

    def test_mdpi_falls_back_to_doi_decoding(self):
        with patch.object(fetch, "_get_json", side_effect=OSError("offline")):
            urls = fetch._mdpi_pdf_candidates("10.3390/jmmp9030084")
        self.assertEqual(urls, ["https://pub.mdpi-res.com/jmmp/jmmp-09-00084/article_deploy/jmmp-09-00084.pdf"])

    def test_plos_template(self):
        self.assertEqual(fetch._try_publisher_direct("10.1371/journal.pone.0171501", timeout=5),
                         [("https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0171501&type=printable", "plos")])

    def test_osti_resolves_fulltext_and_trips_breaker(self):
        fetch._osti_failures = 0
        rec = [{"doi": "10.1/x", "osti_id": "42", "links": [{"rel": "fulltext", "href": "https://www.osti.gov/servlets/purl/42"}]}]
        with patch.object(fetch, "_get_json", return_value=rec):
            self.assertEqual(fetch.try_osti("10.1/x", timeout=5), "https://www.osti.gov/servlets/purl/42")
        with patch.object(fetch, "_get_json", side_effect=TimeoutError("t")):
            for _ in range(fetch.OSTI_MAX_FAILURES):
                self.assertIsNone(fetch.try_osti("10.1/x", timeout=5))
        with patch.object(fetch, "_get_json") as gj:
            self.assertIsNone(fetch.try_osti("10.1/x", timeout=5))
            gj.assert_not_called()
        fetch._osti_failures = 0

    def test_semantic_scholar_sibling_copy(self):
        hits = {"data": [
            {"title": "Other", "externalIds": {"DOI": "10.9/other"}, "openAccessPdf": {"url": "https://x/other.pdf"}},
            {"title": "Growing tissues in microgravity", "externalIds": {"ArXiv": "2301.00001"}},
        ]}
        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": ""}):
            self.assertIsNone(fetch.try_semantic_scholar_copy_by_title("10.1/x", "Growing tissues in microgravity", timeout=5))
        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "k"}), patch.object(fetch, "_get_json", return_value=hits):
            self.assertEqual(fetch.try_semantic_scholar_copy_by_title("10.1/x", "Growing Tissues in Microgravity", timeout=5),
                             "https://arxiv.org/pdf/2301.00001.pdf")

    def test_identifier_lines_map_to_dois(self):
        self.assertEqual(fetch._identifier_to_doi("arXiv:2301.00001v2"), "10.48550/arXiv.2301.00001")
        self.assertEqual(fetch._identifier_to_doi("https://arxiv.org/abs/cond-mat/9712061"), "10.48550/arXiv.cond-mat/9712061")
        with patch.object(fetch, "_get_json", return_value={"records": [{"doi": "10.4137/EHI.S5392", "pmcid": "PMC3000000"}]}):
            self.assertEqual(fetch._identifier_to_doi("PMC3000000"), "10.4137/ehi.s5392")
            self.assertEqual(fetch._identifier_to_doi("PMID 21151592"), "10.4137/ehi.s5392")
        self.assertIsNone(fetch._identifier_to_doi("Growing tissues in microgravity"))


class IdentityGateTests(unittest.TestCase):
    """Record-level DOI trust must not survive a PDF that is another document.

    Every case below is taken from a file a real run had accepted.
    """

    EXPECTED = {
        "doi": "10.1007/978-3-319-50909-9_16-1",
        "title": "Pharmaceutical Liquid Dosage Forms in Space: Looking Toward the Future by Learning from the Past",
        "author": "A. Malhotra",
        "journal": "Handbook of Space Pharmaceuticals",
        "year": 2020,
    }
    OTHER_BOOK_TEXT = ("International Space Station Benefits for Humanity 3rd Edition This book was developed "
                       "collaboratively by the members of the International Space Station Program Science Forum, "
                       "which includes representatives of every space agency taking part in the station programme. ")

    def _verdict(self, pdf_identity):
        import identity
        return identity.validate_article_identity(self.EXPECTED, pdf_identity=pdf_identity, record_doi_matched=True)

    def test_whole_book_served_for_a_chapter_doi_is_rejected(self):
        verdict = self._verdict({"title": "International Space Station Benefits for Humanity, 3rd edition.",
                                 "text_sample": self.OTHER_BOOK_TEXT})
        self.assertFalse(verdict["identity_validated"])
        self.assertEqual(verdict["reason"], "pdf_title_contradicts_record")

    def test_unrelated_document_behind_a_producer_artifact_title_is_rejected(self):
        # "Microsoft Word - Mars_DRA5_Addendum-R3.doc" / "PowerPoint Presentation":
        # the metadata title says nothing, so the opening text has to answer.
        for title in ("Microsoft Word - Mars_DRA5_Addendum-R3 sem a.doc", "PowerPoint Presentation"):
            with self.subTest(title=title):
                verdict = self._verdict({"title": title, "text_sample": self.OTHER_BOOK_TEXT})
                self.assertFalse(verdict["identity_validated"])

    def test_supplementary_material_is_rejected_even_with_the_article_title(self):
        verdict = self._verdict({"title": "Microsoft Word - Supplementary material",
                                 "text_sample": "Supplementary Information Title: " + self.EXPECTED["title"]})
        self.assertFalse(verdict["identity_validated"])
        self.assertEqual(verdict["reason"], "supplementary_material")

    def test_scanned_article_with_a_junk_metadata_title_is_kept(self):
        # Real case: the metadata title is the scanner's filename while the
        # article's own title is on page 1.
        verdict = self._verdict({"title": "D--36-01-9-47-03401.mdi",
                                 "text_sample": self.EXPECTED["title"] + " Chun SONG, Xiu-Qing DUAN"})
        self.assertTrue(verdict["identity_validated"])

    def test_record_trust_survives_weak_or_corroborated_metadata(self):
        for pdf_identity in (
            {},                                                        # nothing extractable
            {"title": None, "text_sample": ""},                        # scan with no text layer
            {"title": "main.pdf"},                                     # filename as title
            {"title": "Pharmaceutical Liquid Dosage Forms in Space"},  # same title, no subtitle
            {"title": "Chapter proof 16", "text_sample": self.OTHER_BOOK_TEXT, "author": "Malhotra"},
            {"title": "Chapter proof 16", "text_sample": self.OTHER_BOOK_TEXT, "year": 2020},
        ):
            with self.subTest(pdf_identity=pdf_identity):
                self.assertTrue(self._verdict(pdf_identity)["identity_validated"])

    def test_conflicting_doi_inside_pdf_still_wins(self):
        verdict = self._verdict({"doi": "10.1038/other", "title": self.EXPECTED["title"]})
        self.assertFalse(verdict["identity_validated"])
        self.assertEqual(verdict["reason"], "doi_mismatch")


class SidecarVersionTests(unittest.TestCase):
    def test_sidecar_from_older_validator_is_ignored(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "a.pdf"
            dest.write_bytes(b"%PDF-1.4")
            fetch._write_identity_sidecar(dest, {"identity_validated": True})
            self.assertEqual(fetch._read_identity_sidecar(dest)["identity_validated"], True)
            stale = json.loads(fetch._identity_sidecar_path(dest).read_text(encoding="utf-8"))
            stale.pop("validator_version")
            fetch._identity_sidecar_path(dest).write_text(json.dumps(stale), encoding="utf-8")
            self.assertIsNone(fetch._read_identity_sidecar(dest))


if __name__ == "__main__":
    unittest.main()
