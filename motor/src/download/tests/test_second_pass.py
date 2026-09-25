"""The second pass over transient failures.

A host that refuses or stalls puts itself on a cooldown (see http_retry), and
every article still queued for that host is then skipped fast so one dead
host cannot eat the whole run. That is the right call during the run — but
those articles were never really tried. In this project's own backlog 74 of
269 failures were exactly that: `download_host_cooldown`, an article dropped
because of someone else's timing.

By the time the run ends the cooldowns have long expired, so the cheapest
recovery available is to ask again, once, with the throttle table cleared.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_parallel


def failure(code: str) -> dict:
    return {"doi": "10.1/x", "success": False, "error": {"code": code}}


class SecondPassSelectionTests(unittest.TestCase):
    def test_cooldown_is_the_clearest_case(self):
        self.assertTrue(run_parallel._deserves_second_pass(failure("download_host_cooldown")))

    def test_transport_hiccups_qualify(self):
        for code in ("download_network_error", "download_timeout",
                     "download_http_429", "download_http_500",
                     "download_http_502", "download_http_503",
                     "worker_exception"):
            with self.subTest(code=code):
                self.assertTrue(run_parallel._deserves_second_pass(failure(code)))

    def test_a_refusal_is_an_answer_and_is_not_retried(self):
        """403/404/401 mean 'no', not 'not now' — asking again just burns time."""
        for code in ("download_http_403", "download_http_404", "download_http_401"):
            with self.subTest(code=code):
                self.assertFalse(run_parallel._deserves_second_pass(failure(code)))

    def test_a_wrong_article_is_not_a_transport_problem(self):
        self.assertFalse(
            run_parallel._deserves_second_pass(failure("article_identity_not_confirmed"))
        )

    def test_no_open_copy_found_is_not_retried(self):
        self.assertFalse(run_parallel._deserves_second_pass(failure("not_found")))

    def test_successes_are_never_retried(self):
        self.assertFalse(run_parallel._deserves_second_pass({"doi": "10.1/x", "success": True}))

    def test_a_result_without_an_error_object_is_not_retried(self):
        self.assertFalse(run_parallel._deserves_second_pass({"doi": "10.1/x", "success": False}))


class RetryableFlagTests(unittest.TestCase):
    def test_fetch_marks_cooldown_as_retryable(self):
        """It is the most retryable failure there is; it used to say otherwise."""
        import fetch

        result = fetch._download_failure(
            "10.1/x", {}, ["unpaywall"],
            [{"source": "unpaywall", "url": "https://h/f.pdf", "reason": "host_cooldown"}],
        )
        self.assertEqual(result["error"]["code"], "download_host_cooldown")
        self.assertTrue(result["error"]["retryable"])


if __name__ == "__main__":
    unittest.main()
