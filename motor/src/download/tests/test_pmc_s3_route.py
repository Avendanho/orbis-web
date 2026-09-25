"""The PMC S3 route driven through fetch(), not around it.

The unit tests for pmc_s3 mock the bucket listing, which is not enough: the
first version of this route rendered a PDF and then crashed calling the
identity gate with the wrong arguments, leaving an unvalidated file on disk.
Nothing that stubs the route out can catch that, so these tests stub only the
network and let the real fetch() chain run.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch
import pmc_s3

JATS = ("""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="doi">10.1002/aur.1227</article-id>
    <title-group><article-title>Peripheral blood gene expression in autism</article-title></title-group>
    <contrib-group><contrib><string-name>Maria Silva</string-name></contrib></contrib-group>
    <abstract><p>We profiled peripheral blood.</p></abstract>
  </article-meta></front>
  <body><sec>""" + "<p>Body sentence for the full text. </p>" * 40 + """</sec></body>
</article>""").encode()


class PmcS3RouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        fetch.reset_credential_refusals()

    def _fetch(self, doi="10.1002/aur.1227", *, xml=JATS, pdf=None):
        """Run fetch() with every earlier source missing and PMC S3 present."""
        assets = {"pmcid": "PMC4530611", "version": 1,
                  "pdf": pdf, "xml": "https://pmc-oa-opendata.s3.amazonaws.com/x.xml" if xml else None,
                  "txt": None}
        with patch.object(fetch, "try_unpaywall", return_value=(None, {})), \
             patch.object(fetch, "try_semantic_scholar", return_value=(None, {}, {})), \
             patch.object(fetch, "try_pmc_idconv", return_value={"pmcid": "PMC4530611", "pmid": "1"}), \
             patch.object(fetch.pmc_s3, "list_assets", return_value=assets), \
             patch.object(fetch.pmc_s3, "fetch_xml", return_value=xml):
            return fetch._fetch_original(
                doi, self.out, dry_run=False, overwrite=True, timeout=10,
                sources=["unpaywall", "semantic_scholar", "pmc_s3"],
            )

    def test_jats_xml_becomes_a_validated_pdf(self):
        result = self._fetch()
        self.assertTrue(result.get("success"), result.get("error"))
        self.assertEqual(result["source"], "pmc_s3_xml")
        self.assertEqual(result["rendered_from"], "jats_xml")
        self.assertTrue(result["identity_validated"])

        pdf = Path(result["file"])
        self.assertTrue(pdf.exists())
        self.assertEqual(pdf.read_bytes()[:5], b"%PDF-")

    def test_the_identity_record_is_written_next_to_the_file(self):
        """Its absence is what exposed the crash in the first version."""
        result = self._fetch()
        sidecar = Path(result["file"] + ".identity.json")
        self.assertTrue(sidecar.exists(), "no identity sidecar was written")

    def test_an_abstract_only_deposit_is_not_accepted_as_full_text(self):
        thin = b"""<?xml version="1.0"?><article><front><article-meta>
            <title-group><article-title>T</article-title></title-group></article-meta></front>
            <body><sec><p>Too short.</p></sec></body></article>"""
        result = self._fetch(xml=thin)
        self.assertFalse(result.get("success"))
        self.assertEqual(list(self.out.glob("*.pdf")), [])

    def test_nothing_is_left_on_disk_when_the_identity_gate_blows_up(self):
        with patch.object(fetch, "_validate_downloaded_file", side_effect=RuntimeError("boom")):
            result = self._fetch()
        self.assertFalse(result.get("success"))
        self.assertEqual(list(self.out.glob("*.pdf")), [], "an unvalidated PDF survived")

    def test_a_wrong_article_is_rejected_and_deleted(self):
        rejected = {"identity_validated": False, "validation_method": "doi_in_pdf",
                    "detected_doi": "10.9999/other", "reason": "doi_mismatch"}
        with patch.object(fetch, "_validate_downloaded_file", return_value=rejected):
            result = self._fetch()
        self.assertFalse(result.get("success"))
        self.assertEqual(list(self.out.glob("*.pdf")), [])

    def test_a_pdf_in_the_bucket_is_preferred_over_the_xml(self):
        calls = []

        def fake_download(url, dest, **kwargs):
            calls.append(url)
            dest.write_bytes(b"%PDF-1.4\n" + b"x" * 200)
            return None

        accepted = {
            "identity_validated": True, "validation_method": "doi_in_pdf",
            "validation_score": 1.0, "detected_doi": "10.1002/aur.1227",
            "detected_title": None, "sha256": "0" * 64, "expected": {},
        }
        with patch.object(fetch, "_download", side_effect=fake_download), \
             patch.object(fetch, "_validate_downloaded_file", return_value=accepted):
            result = self._fetch(pdf="https://pmc-oa-opendata.s3.amazonaws.com/x.pdf")
        self.assertTrue(result.get("success"))
        self.assertEqual(result["source"], "pmc_s3")
        self.assertEqual(calls, ["https://pmc-oa-opendata.s3.amazonaws.com/x.pdf"])

    def test_an_article_outside_the_bucket_falls_through_quietly(self):
        with patch.object(fetch, "try_unpaywall", return_value=(None, {})), \
             patch.object(fetch, "try_semantic_scholar", return_value=(None, {}, {})), \
             patch.object(fetch, "try_pmc_idconv", return_value={"pmcid": "PMC1", "pmid": "1"}), \
             patch.object(fetch.pmc_s3, "list_assets",
                          return_value={"pmcid": "PMC1", "version": None,
                                        "pdf": None, "xml": None, "txt": None}):
            result = fetch._fetch_original(
                "10.1/x", self.out, dry_run=False, overwrite=True, timeout=10,
                sources=["unpaywall", "semantic_scholar", "pmc_s3"],
            )
        self.assertFalse(result.get("success"))
        self.assertIn("pmc_s3", result.get("sources_tried", []))

    def test_a_doi_with_no_pmcid_costs_only_the_lookup(self):
        with patch.object(fetch, "try_unpaywall", return_value=(None, {})), \
             patch.object(fetch, "try_semantic_scholar", return_value=(None, {}, {})), \
             patch.object(fetch, "try_pmc_idconv", return_value={}), \
             patch.object(fetch.pmc_s3, "list_assets") as listed:
            fetch._fetch_original(
                "10.1/x", self.out, dry_run=False, overwrite=True, timeout=10,
                sources=["unpaywall", "semantic_scholar", "pmc_s3"],
            )
        listed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
