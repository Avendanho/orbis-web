"""Offline tests for institutional access, browser gating, full-text XML,
landing-page extraction and the repository resolvers."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_fetch
import fetch
import fulltext_xml
import institutional
from pdf_links import extract_pdf_links

JATS = b"""<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <front><article-meta>
    <title-group><article-title>Chemical risk in spaceflight</article-title></title-group>
    <contrib-group><contrib><string-name>Ana Souza</string-name></contrib></contrib-group>
    <abstract><p>We review chemical exposure aboard spacecraft.</p></abstract>
  </article-meta></front>
  <body><sec><p>%s</p><p>%s</p></sec></body>
</article>""" % (b"Body paragraph one. " * 30, b"Body paragraph two. " * 30)

ELSEVIER_XML = b"""<?xml version="1.0"?>
<full-text-retrieval-response xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:ce="http://www.elsevier.com/xml/common/dtd">
  <coredata><dc:title>Plant growth under microgravity</dc:title>
    <dc:creator>Silva, J.</dc:creator>
    <dc:description>Abstract text.</dc:description></coredata>
  <originalText><ce:para>%s</ce:para><ce:para>%s</ce:para></originalText>
</full-text-retrieval-response>""" % (b"Elsevier body one. " * 30, b"Elsevier body two. " * 30)


class LandingPageExtractionTests(unittest.TestCase):
    def test_extracts_every_exposed_form_in_priority_order(self):
        html = (
            '<meta name="citation_pdf_url" content="/download/1">'
            '<link rel="alternate" type="application/pdf" href="/alt.pdf">'
            '<script type="application/ld+json">{"encoding":{"contentUrl":"https://cdn/x.pdf",'
            '"encodingFormat":"application/pdf"}}</script>'
            '<iframe src="/viewer/embed.pdf"></iframe>'
            '<a type="application/octet-stream" href="/legacy/file">Old repo</a>'
            '<a href="/bitstream/handle/123/4/paper">Full text</a>'
            '<meta name="dc.identifier.uri" content="https://hdl.handle.net/123/4">'
        )
        self.assertEqual(
            extract_pdf_links(html, "https://repo.univ.edu/item/1"),
            [
                "https://repo.univ.edu/download/1",
                "https://repo.univ.edu/alt.pdf",
                "https://cdn/x.pdf",
                "https://repo.univ.edu/viewer/embed.pdf",
                "https://repo.univ.edu/legacy/file",
                "https://repo.univ.edu/bitstream/handle/123/4/paper",
                "https://hdl.handle.net/123/4",
            ],
        )

    def test_ignores_unsafe_and_unrelated_links(self):
        html = ('<a href="javascript:alert(1)">x</a><a href="/about">sobre</a>'
                '<meta name="dc.identifier.uri" content="https://repo.univ.edu/item/1">')
        self.assertEqual(extract_pdf_links(html, "https://repo.univ.edu/item/1"), [])


class InstitutionalAccessTests(unittest.TestCase):
    def setUp(self):
        for var in ("PAPER_FETCH_PROXY", "PROXY_URL", "HTTPS_PROXY", "https_proxy",
                    "HTTP_PROXY", "http_proxy", "EZPROXY_BASE_URL"):
            os.environ.pop(var, None)

    def test_no_configuration_is_a_no_op(self):
        self.assertFalse(institutional.is_configured())
        self.assertEqual(institutional.fetch_pdf("https://publisher/x.pdf"), (None, "not_configured"))

    def test_proxy_variable_precedence(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://b:3128", "PAPER_FETCH_PROXY": "socks5://a:1080"}):
            self.assertEqual(institutional.proxy_url(), "socks5://a:1080")
        with patch.dict(os.environ, {"HTTP_PROXY": "http://b:3128"}):
            self.assertEqual(institutional.proxy_url(), "http://b:3128")
            self.assertTrue(institutional.is_configured())

    def test_ezproxy_url_and_loop_guard(self):
        with patch.dict(os.environ, {"EZPROXY_BASE_URL": "https://ezproxy.pucminas.br/"}):
            self.assertEqual(
                institutional.ezproxy_url("https://www.sciencedirect.com/a.pdf"),
                "https://ezproxy.pucminas.br/login?url=https%3A%2F%2Fwww.sciencedirect.com%2Fa.pdf",
            )
            # An already-proxied URL must not be wrapped again.
            self.assertIsNone(institutional.ezproxy_url("https://x.ezproxy.pucminas.br/a.pdf"))

    def test_login_form_hidden_fields_are_preserved(self):
        html = '<form><input type="hidden" name="url" value="https://a/b"><input type="text" name="user"></form>'
        self.assertEqual(institutional._login_fields(html), {"url": "https://a/b"})

    def test_only_licence_shaped_failures_trigger_the_retry(self):
        for error in ("http_403", "http_401", "not_a_pdf"):
            self.assertTrue(fetch._institutional_retry_warranted(error))
        for error in ("http_404", "network_error", None):
            self.assertFalse(fetch._institutional_retry_warranted(error))


class BrowserGatingTests(unittest.TestCase):
    def test_disabled_unless_operator_opts_in(self):
        with patch.dict(os.environ, {"PAPER_FETCH_BROWSER": ""}):
            self.assertFalse(browser_fetch.is_enabled())
            self.assertEqual(browser_fetch.fetch_pdf("https://repositorio.ufmg.br/x")[1], "browser_disabled")

    def test_allowlist_covers_repositories_only(self):
        allowed = ["https://repositorio.ufmg.br/handle/1", "https://eprints.keele.ac.uk/1",
                   "https://escholarship.org/uc/item/1", "https://research.library.harvard.edu/x",
                   "https://zenodo.org/record/1"]
        denied = ["https://sci-hub.se/10.1/x", "https://annas-archive.org/x",
                  "https://libgen.li/get.php", "https://www.sciencedirect.com/x.pdf"]
        for url in allowed:
            self.assertTrue(browser_fetch.host_allowed(url), url)
        for url in denied:
            self.assertFalse(browser_fetch.host_allowed(url), url)

    def test_operator_can_add_hosts_explicitly(self):
        with patch.dict(os.environ, {"PAPER_FETCH_BROWSER_HOSTS": "www.mdpi.com"}):
            self.assertTrue(browser_fetch.host_allowed("https://www.mdpi.com/1/pdf"))

    def test_user_agent_identifies_the_tool_and_contact(self):
        with patch.dict(os.environ, {"UNPAYWALL_EMAIL": "eu@puc.br"}):
            ua = browser_fetch.user_agent()
        self.assertIn("revisao-sistematica", ua)
        self.assertIn("eu@puc.br", ua)


class FullTextXmlTests(unittest.TestCase):
    def test_parses_jats_body(self):
        article = fulltext_xml.parse_fulltext(JATS)
        self.assertEqual(article["title"], "Chemical risk in spaceflight")
        self.assertEqual(article["authors"], ["Ana Souza"])
        self.assertEqual(len(article["paragraphs"]), 2)  # os dois <p> do <body>
        self.assertIn("chemical exposure", article["abstract"])

    def test_parses_elsevier_body(self):
        article = fulltext_xml.parse_fulltext(ELSEVIER_XML)
        self.assertEqual(article["title"], "Plant growth under microgravity")
        self.assertEqual(len(article["paragraphs"]), 2)

    def test_rejects_documents_without_a_real_body(self):
        self.assertIsNone(fulltext_xml.parse_fulltext(b"<article><body><p>curto</p></body></article>"))
        self.assertIsNone(fulltext_xml.parse_fulltext(b"not xml"))

    def test_renders_a_pdf_carrying_the_doi(self):
        import pypdf
        article = fulltext_xml.parse_fulltext(JATS)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out.pdf"
            self.assertTrue(fulltext_xml.render_pdf(article, "10.1007/abc", dest))
            text = pypdf.PdfReader(str(dest)).pages[0].extract_text()
        self.assertIn("10.1007/abc", text)
        self.assertIn("Chemical risk", text)

    def test_api_is_skipped_for_other_publishers(self):
        with patch.object(fulltext_xml, "try_elsevier_xml") as els, patch.object(fulltext_xml, "try_springer_jats") as spr:
            ok, error = fulltext_xml.fetch_as_pdf("10.1234/unrelated", Path("/tmp/x.pdf"))
        self.assertFalse(ok)
        self.assertEqual(error, "no_applicable_api")
        els.assert_not_called()
        spr.assert_not_called()


class PublisherRouteTests(unittest.TestCase):
    def test_open_access_publisher_templates(self):
        self.assertEqual(fetch._try_publisher_direct("10.3389/fmicb.2025.1691537", timeout=5),
                         [("https://www.frontiersin.org/articles/10.3389/fmicb.2025.1691537/pdf", "frontiers")])
        self.assertEqual(fetch._try_publisher_direct("10.7717/peerj.16678", timeout=5),
                         [("https://peerj.com/articles/16678.pdf", "peerj")])
        self.assertEqual(fetch._try_publisher_direct("10.7717/peerj-cs.1234", timeout=5),
                         [("https://peerj.com/articles/cs-1234.pdf", "peerj")])

    def test_mdpi_falls_back_to_the_publisher_site_last(self):
        with patch.object(fetch, "_get_json", side_effect=OSError("offline")):
            candidates = fetch._try_publisher_direct("10.3390/jmmp9030084", timeout=5)
        self.assertEqual(candidates[0][0],
                         "https://pub.mdpi-res.com/jmmp/jmmp-09-00084/article_deploy/jmmp-09-00084.pdf")
        self.assertEqual(candidates[-1], ("https://www.mdpi.com/jmmp9030084/pdf", "mdpi"))


class RepositoryResolverTests(unittest.TestCase):
    def test_fatcat_requires_a_matching_doi_and_stops_after_failures(self):
        release = {"doi": "10.1000/x", "title": "T", "release_year": 2020,
                   "files": [{"mimetype": "application/pdf", "urls": [{"url": "https://web.archive.org/a.pdf"}]}]}
        fetch._fatcat_failures = 0
        with patch.object(fetch, "_get_json", return_value=release):
            urls, meta, _raw = fetch.try_fatcat("10.1000/x", timeout=5)
        self.assertEqual(urls, ["https://web.archive.org/a.pdf"])
        self.assertEqual(meta["year"], 2020)
        with patch.object(fetch, "_get_json", return_value={"doi": "10.1000/other", "files": []}):
            self.assertEqual(fetch.try_fatcat("10.1000/x", timeout=5)[0], [])
        with patch.object(fetch, "_get_json", side_effect=OSError("down")):
            for _ in range(fetch.FATCAT_MAX_FAILURES):
                fetch.try_fatcat("10.1000/x", timeout=5)
        with patch.object(fetch, "_get_json") as get_json:
            fetch.try_fatcat("10.1000/x", timeout=5)
            get_json.assert_not_called()
        fetch._fatcat_failures = 0

    def test_base_disables_itself_when_the_ip_is_not_registered(self):
        import urllib.error
        fetch._base_blocked = False
        with patch.object(fetch, "_get_json", side_effect=urllib.error.HTTPError("u", 401, "no", {}, None)):
            self.assertEqual(fetch.try_base_search("10.1000/x", timeout=5), ([], {}, []))
        self.assertTrue(fetch._base_blocked)
        with patch.object(fetch, "_get_json") as get_json:
            fetch.try_base_search("10.1000/x", timeout=5)
            get_json.assert_not_called()
        fetch._base_blocked = False

    def test_base_also_detects_the_refusal_sent_as_http_200(self):
        fetch._base_blocked = False
        denied = {"error": "Access denied for IP address 200.198.55.6 and user agent x."}
        with patch.object(fetch, "_get_json", return_value=denied):
            self.assertEqual(fetch.try_base_search("10.1000/x", timeout=5), ([], {}, []))
        self.assertTrue(fetch._base_blocked)
        fetch._base_blocked = False

    def test_figshare_only_uses_records_whose_doi_matches(self):
        listing = [{"id": 1, "doi": "10.1000/x", "title": "T"}, {"id": 2, "doi": "10.1000/other", "title": "O"}]
        detail = {"files": [{"name": "paper.pdf", "download_url": "https://figshare/1.pdf", "mimetype": "application/pdf"}],
                  "title": "T", "authors": [{"last_name": "Souza"}], "published_date": "2021-05-01"}
        with patch.object(fetch, "_get_json", side_effect=[listing, detail]):
            urls, meta, _raw = fetch.try_figshare("10.1000/x", timeout=5)
        self.assertEqual(urls, ["https://figshare/1.pdf"])
        self.assertEqual(meta["year"], "2021")


class SourceStatsTests(unittest.TestCase):
    def test_attempts_are_tallied_per_source(self):
        fetch._stats_reset()
        fetch._stats_record("unpaywall", ms=1200, ok=False, error="http_403")
        fetch._stats_record("unpaywall", ms=800, ok=True)
        fetch._stats_record("pmc", ms=500, ok=True)
        snapshot = fetch._stats_snapshot()
        self.assertEqual(snapshot["unpaywall"]["attempts"], 2)
        self.assertEqual(snapshot["unpaywall"]["ok"], 1)
        self.assertEqual(snapshot["unpaywall"]["last_error"], "http_403")
        self.assertEqual(snapshot["pmc"], {"attempts": 1, "ok": 1, "failed": 0, "ms": 500, "last_error": None})


if __name__ == "__main__":
    unittest.main()
