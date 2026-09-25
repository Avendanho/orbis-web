"""Testes offline do registro de bases, da tradução de queries e dos conectores novos."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import query_utils
from bases import BASES, resolve_base
from connectors import base_search, europepmc, oasisbr, scopus, semantic_scholar
from main import ler_queries_do_arquivo, query_for

PUBMED_QUERY = '("microgravity"[MeSH Terms] OR microgravity[tiab]) and cartilage[Title/Abstract]'


class QueryTranslationTests(unittest.TestCase):
    def test_pubmed_tags_removed(self):
        self.assertEqual(query_utils.to_plain_boolean(PUBMED_QUERY), '("microgravity" OR microgravity) AND cartilage')

    def test_semantic_scholar_operators(self):
        self.assertEqual(semantic_scholar.to_s2_syntax("(a OR b) AND c NOT d"), "(a | b) + c -d")

    def test_scopus_wraps_plain_query_but_keeps_native_syntax(self):
        self.assertEqual(scopus.to_scopus_query("a[tiab] AND b"), "TITLE-ABS-KEY(a AND b)")
        native = "TITLE(a) AND PUBYEAR > 2010"
        self.assertEqual(scopus.to_scopus_query(native), native)

    def test_clean_doi(self):
        self.assertEqual(query_utils.clean_doi("https://doi.org/10.1234/ab.c."), "10.1234/ab.c")
        self.assertIsNone(query_utils.clean_doi("sem doi"))


class RegistryTests(unittest.TestCase):
    def test_resolve_by_key_or_label(self):
        self.assertEqual(resolve_base("pubmed"), "PUBMED")
        self.assertEqual(resolve_base("Semantic Scholar"), "SEMANTICSCHOLAR")
        self.assertEqual(resolve_base("europe-pmc"), "EUROPEPMC")
        self.assertIsNone(resolve_base("google"))

    def test_labels_are_single_words(self):
        # O front-end e o backend reconhecem o resumo "<Base>: N artigos" com \\w+.
        for label, _fn in BASES.values():
            self.assertRegex(label, r"^\w+$")

    def test_sections_and_fallback_query(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("[PUBMED]\nq_pubmed\n[SCOPUS]\nTITLE(x)\n")
        try:
            queries = ler_queries_do_arquivo(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(queries, {"PUBMED": "q_pubmed", "SCOPUS": "TITLE(x)"})
        self.assertEqual(query_for(queries, "Scopus"), "TITLE(x)")
        # Base sem seção nem DEFAULT usa a primeira query presente.
        self.assertEqual(query_for(queries, "OpenAlex"), "q_pubmed")


class ConnectorTests(unittest.TestCase):
    def test_europepmc_paginates_with_cursor(self):
        pages = [
            {"hitCount": 3, "nextCursorMark": "c1", "resultList": {"result": [{"doi": "10.1000/a"}, {"title": "T sem doi"}]}},
            {"hitCount": 3, "nextCursorMark": "c1", "resultList": {"result": [{"doi": "10.1000/b"}]}},
        ]
        with patch.object(europepmc, "get_json", side_effect=pages) as gj:
            total, dois, no_doi = europepmc.fetch_europepmc_dois("x")
        self.assertEqual((total, dois, no_doi), (3, ["10.1000/a", "10.1000/b"], ["T sem doi"]))
        self.assertEqual(gj.call_args_list[1].kwargs["params"]["cursorMark"], "c1")

    def test_oasisbr_extracts_doi_from_urls(self):
        page = {"resultCount": 2, "records": [
            {"title": "A", "urls": [{"url": "https://doi.org/10.5555/abc"}]},
            {"title": "B", "urls": [{"url": "http://repositorio.x/handle/1"}]},
        ]}
        with patch.object(oasisbr, "get_json", return_value=page):
            self.assertEqual(oasisbr.fetch_oasisbr_dois("x"), (2, ["10.5555/abc"], ["B"]))

    def test_base_reports_the_ip_refusal_without_breaking_the_run(self):
        # A BASE recusa IP não cadastrado com HTTP 200 e um payload de erro.
        denied = {"error": "Access denied for IP address 200.198.55.6 and user agent x."}
        with patch.object(base_search, "get_json", return_value=denied):
            self.assertEqual(base_search.fetch_base_dois("microgravity"), (0, [], []))

    def test_base_extracts_dois_and_titles(self):
        page = {"response": {"numFound": 2, "docs": [
            {"dcdoi": "10.1000/a", "dctitle": ["Com DOI"]},
            {"dctitle": ["Sem DOI"]},
        ]}}
        with patch.object(base_search, "get_json", return_value=page):
            self.assertEqual(base_search.fetch_base_dois("x"), (2, ["10.1000/a"], ["Sem DOI"]))

    def test_scopus_without_key_is_skipped(self):
        with patch.dict(os.environ, {"SCOPUS_API_KEY": "", "ELSEVIER_API_KEY": ""}):
            self.assertEqual(scopus.fetch_scopus_dois("x"), (0, [], []))


if __name__ == "__main__":
    unittest.main()
