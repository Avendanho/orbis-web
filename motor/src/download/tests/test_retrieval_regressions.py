"""Offline retrieval regressions. Run with unittest; no external services required."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch
from pdf_links import extract_pdf_links
from bypass403 import GoByPASS403Engine

class Response:
    def __init__(self, body, url): self.body, self.url = body, url
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, *args): return self.body
    def geturl(self): return self.url

class RetrievalTests(unittest.TestCase):
    def setUp(self):
        fetch._format = 'silent'

    def test_metadata_query_relative_and_unsafe_links(self):
        result = extract_pdf_links('<meta name="citation_pdf_url" content="/download/1"><a href="paper.pdf?x=1&amp;y=2">PDF</a><a type="application/pdf" href="javascript:alert(1)">bad</a>', 'https://example.org/article/')
        self.assertEqual(result, ['https://example.org/download/1', 'https://example.org/article/paper.pdf?x=1&y=2'])

    def test_explicit_pdf_doi_metadata(self):
        import pypdf
        import identity
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.add_metadata({"/doi": "10.1234/correct"})
        stream = io.BytesIO()
        writer.write(stream)
        self.assertEqual(identity.extract_pdf_identity(stream.getvalue())["doi"], "10.1234/correct")

    def test_unpaywall_keeps_alternatives(self):
        with patch.object(fetch, '_get_json', return_value={'best_oa_location': {'url_for_pdf':'https://x/a.pdf'}, 'oa_locations':[{'url_for_pdf':'https://x/b.pdf'}]}):
            first, meta = fetch.try_unpaywall('10.1234/a', timeout=1)
        self.assertEqual(first, 'https://x/a.pdf')
        self.assertEqual(meta['pdf_candidates'], ['https://x/a.pdf', 'https://x/b.pdf'])

    def test_failed_first_copy_uses_second_copy(self):
        doi = '10.1234/right'
        calls = []
        def download(url, dest, **kwargs):
            calls.append(url)
            if url.endswith('a.pdf'): return 'http_404'
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b'validated by mocked identity gate')
            return None
        verdict = {'identity_validated':True, 'validation_method':'doi_in_pdf', 'validation_score':1, 'expected':{'doi':doi}}
        meta = {'title':'Correct title', 'author':'Doe','pdf_candidates':['https://x/a.pdf','https://x/b.pdf']}
        with tempfile.TemporaryDirectory() as d, patch.object(fetch, 'EMAIL','test@example.org'), patch.object(fetch, 'try_unpaywall',return_value=('https://x/a.pdf',meta)), patch.object(fetch,'_download',side_effect=download), patch.object(fetch,'_validate_downloaded_file',return_value=verdict):
            result = fetch._fetch_original(doi,Path(d),dry_run=False,overwrite=False,timeout=1,sources=['unpaywall'])
        self.assertTrue(result['success'])
        self.assertEqual(result['pdf_url'],'https://x/b.pdf')
        self.assertEqual(len(calls),2)

    def test_landing_page_to_pdf(self):
        pdf = b'%PDF simulated'
        def open_url(req, **kwargs):
            if req.full_url.endswith('/article'):
                return Response(b'<meta name="citation_pdf_url" content="/download/1">',req.full_url)
            return Response(pdf,req.full_url)
        with tempfile.TemporaryDirectory() as d, patch.object(fetch,'_url_fetch_allowed',return_value=(True,'')), patch.object(fetch,'bypass_download_pdf',return_value=(False,'not_a_pdf')), patch.object(fetch,'_is_cloak_enabled',return_value=False), patch.object(fetch.urllib.request,'urlopen',side_effect=open_url), patch.object(fetch,'validate_pdf_data',side_effect=lambda b:(b==pdf,b,'not_a_pdf')):
            dest = Path(d)/'article.pdf'
            self.assertIsNone(fetch._download('https://example.org/article',dest,timeout=1))
            self.assertEqual(dest.read_bytes(),pdf)

    def test_landing_page_cycle_stops(self):
        page=Response(b'<meta name="citation_pdf_url" content="/article">','https://example.org/article')
        with tempfile.TemporaryDirectory() as d, patch.object(fetch,'_url_fetch_allowed',return_value=(True,'')), patch.object(fetch,'bypass_download_pdf',return_value=(False,'not_a_pdf')), patch.object(fetch,'_is_cloak_enabled',return_value=False), patch.object(fetch.urllib.request,'urlopen',return_value=page) as call:
            self.assertIsNotNone(fetch._download(page.url,Path(d)/'a.pdf',timeout=1))
            self.assertEqual(call.call_count,1)

    def test_bypass_timeout_falls_back_to_urllib(self):
        pdf=b'%PDF fallback'
        with tempfile.TemporaryDirectory() as d, patch.object(fetch,'_url_fetch_allowed',return_value=(True,'')), patch.object(fetch,'bypass_download_pdf',return_value=(False,'timeout')), patch.object(fetch.urllib.request,'urlopen',return_value=Response(pdf,'https://example.org/a.pdf')), patch.object(fetch,'validate_pdf_data',side_effect=lambda b:(b==pdf,b,'not_a_pdf')):
            dest=Path(d)/'a.pdf'
            self.assertIsNone(fetch._download('https://example.org/a.pdf',dest,timeout=1))
            self.assertEqual(dest.read_bytes(),pdf)

    def test_repository_preserves_download_url(self):
        with patch.object(fetch,'try_hal',return_value=(['https://example.org/file/1'],{'title':'Study','doi':'10.1234/a'},[])):
            records=fetch._search_title_source('hal','Study',1)
        ranked=fetch._rank_title_recovery_candidates('Study',records)
        self.assertIn('https://example.org/file/1',ranked[0]['pdf_candidates'])

    def test_merge_keeps_both_copies(self):
        records=[{'title':'Study','doi':'10.1234/a','resolver':'hal','download_url':['https://x/file/1']},{'title':'Study','doi':'10.1234/a','resolver':'zenodo','download_url':['https://x/file/2']}]
        self.assertEqual(len(fetch._rank_title_recovery_candidates('Study',records)[0]['pdf_candidates']),2)

    def test_chapter_doi_preserved(self):
        doi='10.1234/book.ch3'
        with tempfile.TemporaryDirectory() as d, patch.object(fetch,'EMAIL','test@example.org'), patch.object(fetch,'try_unpaywall',return_value=('https://x/a.pdf',{'title':'Chapter','author':'Doe'})) as resolver:
            result=fetch._fetch_original(doi,Path(d),dry_run=True,overwrite=False,timeout=1,sources=['unpaywall'])
        self.assertEqual(result['doi'],doi)
        self.assertEqual(resolver.call_args.args[0],doi)

    def test_http_429_not_multiplied(self):
        engine=GoByPASS403Engine()
        engine.enable_curl=True
        with patch.object(engine,'_exec_requests',return_value=(429,b'','')) as request, patch.object(engine,'_exec_curl') as curl:
            result=engine.execute_request('https://example.org/a',timeout=25)
        self.assertEqual(result.error,'http_429')
        self.assertEqual(request.call_count,1)
        curl.assert_not_called()

    def test_doi_resolver_metadata_link(self):
        with patch.object(fetch.urllib.request,'urlopen',return_value=Response(b'<meta name="citation_pdf_url" content="/download/1">','https://example.org/article')):
            url, meta=fetch.try_doi_resolver('10.1234/a',timeout=1)
        self.assertEqual(url,'https://example.org/download/1')
        self.assertEqual(meta['pdf_candidates'],[url])

if __name__=='__main__': unittest.main()
