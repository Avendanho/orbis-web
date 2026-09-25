"""The second pass, exercised through the real orchestrator.

The delicate part is not deciding what to retry — it is the bookkeeping. A
recovered item has already been counted once as a failure, so the totals,
the per-source tally and the progress bar all have to be walked back before
the new verdict is recorded, or the run reports more items than it processed.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_parallel


def cooldown_failure(doi: str) -> dict:
    return {
        "doi": doi, "success": False, "source": "unpaywall",
        "error": {"code": "download_host_cooldown", "message": "host on cooldown", "retryable": True},
    }


def refusal(doi: str) -> dict:
    return {
        "doi": doi, "success": False, "source": "publisher_direct",
        "error": {"code": "download_http_403", "message": "forbidden", "retryable": False},
    }


def success(doi: str, out_dir: Path) -> dict:
    return {
        "doi": doi, "success": True, "source": "pmc_s3",
        "file": str(out_dir / f"{doi.replace('/', '_')}.pdf"),
        "identity_validated": True,
    }


class SecondPassOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, dois, behaviour):
        """Run the orchestrator with a scripted per-DOI outcome sequence."""
        calls = {"n": 0}

        def fake(doi, out_dir, **kwargs):
            calls["n"] += 1
            return behaviour(doi, calls["n"])

        with patch.object(run_parallel, "_process_single_doi", side_effect=fake), \
             patch.object(run_parallel, "_emit_event"), \
             patch.object(run_parallel.TerminalProgressBar, "stop"), \
             patch.object(run_parallel, "_write_bibtex"), \
             patch("builtins.print"):
            doi_results, title_results = run_parallel.run_parallel_workers(
                dois, [], self.out_dir, max_workers=2, timeout=5,
            )
        return doi_results, calls["n"]

    def test_a_cooldown_failure_is_retried_and_can_succeed(self):
        attempts = {}

        def behaviour(doi, _n):
            attempts[doi] = attempts.get(doi, 0) + 1
            return cooldown_failure(doi) if attempts[doi] == 1 else success(doi, self.out_dir)

        results, total_calls = self._run(["10.1/a"], behaviour)
        self.assertEqual(total_calls, 2, "the item should have been tried twice")
        self.assertTrue(results[0]["success"])
        self.assertTrue(results[0]["second_pass"])
        self.assertEqual(results[0]["source"], "pmc_s3")

    def test_a_403_is_not_retried(self):
        results, total_calls = self._run(["10.1/a"], lambda doi, _n: refusal(doi))
        self.assertEqual(total_calls, 1)
        self.assertFalse(results[0]["success"])

    def test_a_still_failing_retry_keeps_the_original_verdict(self):
        results, total_calls = self._run(["10.1/a"], lambda doi, _n: cooldown_failure(doi))
        self.assertEqual(total_calls, 2)
        self.assertFalse(results[0]["success"])
        self.assertNotIn("second_pass", results[0])

    def test_results_stay_in_input_order_after_a_recovery(self):
        attempts = {}

        def behaviour(doi, _n):
            attempts[doi] = attempts.get(doi, 0) + 1
            if doi == "10.1/b" and attempts[doi] == 1:
                return cooldown_failure(doi)
            return success(doi, self.out_dir)

        results, _ = self._run(["10.1/a", "10.1/b", "10.1/c"], behaviour)
        self.assertEqual([r["doi"] for r in results], ["10.1/a", "10.1/b", "10.1/c"])
        self.assertTrue(all(r["success"] for r in results))

    def test_one_item_is_never_counted_twice(self):
        attempts = {}

        def behaviour(doi, _n):
            attempts[doi] = attempts.get(doi, 0) + 1
            return cooldown_failure(doi) if attempts[doi] == 1 else success(doi, self.out_dir)

        results, _ = self._run(["10.1/a", "10.1/b"], behaviour)
        self.assertEqual(len(results), 2)
        self.assertEqual(len({r["doi"] for r in results}), 2)


class ProgressRetractTests(unittest.TestCase):
    def test_retract_undoes_a_failure(self):
        bar = run_parallel.TerminalProgressBar(2)
        with patch("builtins.print"):
            bar.update({"doi": "10.1/a", "success": False})
        self.assertEqual((bar.current, bar.failed), (1, 1))
        bar.retract({"doi": "10.1/a", "success": False})
        self.assertEqual((bar.current, bar.failed), (0, 0))

    def test_retract_undoes_a_download_and_its_source_tally(self):
        bar = run_parallel.TerminalProgressBar(1)
        with patch("builtins.print"):
            bar.update({"doi": "10.1/a", "success": True, "source": "pmc_s3"})
        name = run_parallel._format_source_name("pmc_s3")
        self.assertEqual(bar.sources_count[name], 1)
        bar.retract({"doi": "10.1/a", "success": True, "source": "pmc_s3"})
        self.assertEqual((bar.current, bar.downloaded), (0, 0))
        self.assertEqual(bar.sources_count[name], 0)

    def test_retract_never_goes_negative(self):
        bar = run_parallel.TerminalProgressBar(1)
        bar.retract({"doi": "10.1/a", "success": False})
        self.assertEqual((bar.current, bar.failed), (0, 0))


if __name__ == "__main__":
    unittest.main()
