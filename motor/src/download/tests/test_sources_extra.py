"""Offline tests for the DOI-derived routes and the title-search sources."""
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch
import sources_extra

SCHOLAR_PAGE = """
<div class="gs_r gs_or gs_scl" data-cid="a">
  <div class="gs_ggs gs_fl"><div class="gs_ggsd"><div class="gs_or_ggsm"><a href="https://ex.org/a.pdf?x=1&amp;y=2">[PDF] ex.org</a></div></div></div>
  <div class="gs_ri"><h3 class="gs_rt"><span>[PDF]</span> <a href="#">Regulation of translation initiation in eukaryotes</a></h3></div>
</div>
<div class="gs_r gs_or gs_scl" data-cid="b">
  <div class="gs_or_ggsm"><a href="https://other.org/b.pdf">[PDF]</a></div>
  <h3 class="gs_rt"><a href="#">A completely different paper about bridges</a></h3>
</div>
"""

ARXIV_PAGE = """
<li class="arxiv-result"><a href="https://arxiv.org/abs/2104.04692">arXiv:2104.04692</a>
<p class="title is-5 mathjax">Not All Attention Is All You Need</p></li>
<li class="arxiv-result"><a href="https://arxiv.org/abs/1706.03762v7">arXiv:1706.03762</a>
<p class="title is-5 mathjax">
  Attention Is All You Need
</p></li>
"""


class DoiPatternTests(unittest.TestCase):
    def test_derived_urls(self):
        cases = {
            "10.48550/arXiv.1706.03762": "https://arxiv.org/pdf/1706.03762",
            "10.31234/osf.io/47dx2_v1": "https://osf.io/47dx2/download",
            "10.17605/OSF.IO/ABCDE": "https://osf.io/abcde/download",
            "10.21203/rs.3.rs-4840802/v1": "https://www.researchsquare.com/article/rs-4840802/v1.pdf",
            "10.20944/preprints202407.1143.v1": "https://www.preprints.org/manuscript/202407.1143/v1/download",
            "10.2139/ssrn.5238498": "https://papers.ssrn.com/sol3/Delivery.cfm?abstractid=5238498",
            "10.5194/acp-20-4809-2020": "https://acp.copernicus.org/articles/20/4809/2020/acp-20-4809-2020.pdf",
            "10.1145/3290605.3300233": "https://dl.acm.org/doi/pdf/10.1145/3290605.3300233",
        }
        for doi, expected in cases.items():
            with self.subTest(doi=doi):
                self.assertEqual(sources_extra.doi_pattern_urls(doi), [expected])

    def test_unrelated_doi_has_no_route(self):
        self.assertEqual(sources_extra.doi_pattern_urls("10.1016/j.cell.2009.01.042"), [])

    def test_elife_uses_api_pdf_field(self):
        body = json.dumps({"pdf": "https://cdn.elifesciences.org/articles/09560/elife-09560-v1.pdf"}).encode()
        with patch.object(fetch, "_get", return_value=body):
            urls, _, _ = sources_extra.try_doi_patterns("10.7554/eLife.09560")
        self.assertEqual(urls, ["https://cdn.elifesciences.org/articles/09560/elife-09560-v1.pdf"])


class TitleSearchTests(unittest.TestCase):
    def setUp(self):
        fetch._format = "silent"
        sources_extra._scholar_blocked = False
        sources_extra._scholar_last = 0.0

    def test_scholar_keeps_only_matching_title(self):
        with patch.object(fetch, "_get", return_value=SCHOLAR_PAGE.encode()):
            urls, _, _ = sources_extra.try_google_scholar(title="Regulation of translation initiation in eukaryotes")
        self.assertEqual(urls, ["https://ex.org/a.pdf?x=1&y=2"])

    def test_scholar_captcha_disables_source(self):
        with patch.object(fetch, "_get", return_value=b"<div id='gs_captcha_f'>gs_captcha</div>") as get:
            sources_extra.try_google_scholar(title="Some reasonably long paper title")
            sources_extra.try_google_scholar(title="Another reasonably long paper title")
        self.assertEqual(get.call_count, 1)
        self.assertTrue(sources_extra._scholar_blocked)

    def test_scholar_429_disables_source(self):
        err = urllib.error.HTTPError("u", 429, "Too Many", {}, None)
        with patch.object(fetch, "_get", side_effect=err):
            sources_extra.try_google_scholar(title="Some reasonably long paper title")
        self.assertTrue(sources_extra._scholar_blocked)

    def test_arxiv_title_prefers_exact_match(self):
        with patch.object(fetch, "_get", return_value=ARXIV_PAGE.encode()):
            urls, meta, _ = sources_extra.try_arxiv_title(title="Attention Is All You Need")
        self.assertEqual(urls[0], "https://arxiv.org/pdf/1706.03762")
        self.assertEqual(meta["arxiv_id"], "1706.03762")

    def test_cyberleninka_builds_pdf_url_for_match(self):
        payload = {"articles": [
            {"name": "<b>Нейронные</b> сети в задачах прогнозирования временных рядов", "link": "/article/n/abc"},
            {"name": "Совсем другая статья про мосты", "link": "/article/n/xyz"},
        ]}
        resp = MagicMock()
        resp.read.return_value = json.dumps(payload).encode()
        resp.__enter__.return_value = resp
        with patch("urllib.request.urlopen", return_value=resp):
            urls, _, _ = sources_extra.try_cyberleninka(title="Нейронные сети в задачах прогнозирования временных рядов")
        self.assertEqual(urls, ["https://cyberleninka.ru/article/n/abc/pdf"])

    def test_truncated_title_matches_by_prefix(self):
        self.assertTrue(sources_extra._title_ok(
            "Regulation of translation initiation in eukaryotes: mechanisms and biological targets",
            "Regulation of translation initiation in eukaryotes: mechanisms …",
        ))


if __name__ == "__main__":
    unittest.main()
