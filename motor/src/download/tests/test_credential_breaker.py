"""Give up on a publisher API whose key turns out to carry no entitlement.

Measured on this project's own run: 43 requests went to api.elsevier.com and
every one of them was refused. The Entitlement API explains why —

    {"service-error": {"status": {"statusCode": "AUTHENTICATION_ERROR",
     "statusText": "Requestor configuration settings insufficient..."}}}

— the key is registered but not tied to a subscribing institution. That is a
property of the credential, not of the article, so the answer is the same for
every DOI. After a few identical refusals the route is dropped for the rest
of the run instead of being asked once per article.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch


class CredentialBreakerTests(unittest.TestCase):
    def setUp(self):
        fetch.reset_credential_refusals()
        self.addCleanup(fetch.reset_credential_refusals)

    def test_a_credentialed_host_starts_usable(self):
        self.assertTrue(fetch.credentialed_api_usable("https://api.elsevier.com/content/article/doi/10.1/x"))

    def test_it_survives_a_couple_of_refusals(self):
        for _ in range(fetch.CREDENTIAL_GIVE_UP_AFTER - 1):
            fetch.note_credential_refusal("https://api.elsevier.com/content/article/doi/10.1/x", "http_403")
        self.assertTrue(fetch.credentialed_api_usable("https://api.elsevier.com/content/article/doi/10.1/y"))

    def test_it_gives_up_once_the_pattern_is_unmistakable(self):
        for _ in range(fetch.CREDENTIAL_GIVE_UP_AFTER):
            fetch.note_credential_refusal("https://api.elsevier.com/content/article/doi/10.1/x", "http_403")
        self.assertFalse(fetch.credentialed_api_usable("https://api.elsevier.com/content/article/doi/10.1/y"))

    def test_giving_up_on_one_publisher_does_not_affect_another(self):
        for _ in range(fetch.CREDENTIAL_GIVE_UP_AFTER):
            fetch.note_credential_refusal("https://api.elsevier.com/x", "http_403")
        self.assertTrue(fetch.credentialed_api_usable("https://api.wiley.com/onlinelibrary/tdm/v1/articles/10.1/x"))

    def test_only_authorization_refusals_count(self):
        """A timeout or a 500 says nothing about the credential."""
        for reason in ("timeout", "network_error", "http_500", "not_a_pdf"):
            fetch.reset_credential_refusals()
            for _ in range(fetch.CREDENTIAL_GIVE_UP_AFTER + 2):
                fetch.note_credential_refusal("https://api.elsevier.com/x", reason)
            with self.subTest(reason=reason):
                self.assertTrue(fetch.credentialed_api_usable("https://api.elsevier.com/y"))

    def test_hosts_without_a_credential_are_never_gated(self):
        for _ in range(fetch.CREDENTIAL_GIVE_UP_AFTER * 3):
            fetch.note_credential_refusal("https://link.springer.com/content/pdf/10.1/x.pdf", "http_403")
        self.assertTrue(fetch.credentialed_api_usable("https://link.springer.com/content/pdf/10.1/y.pdf"))


class PublisherDirectTests(unittest.TestCase):
    def setUp(self):
        fetch.reset_credential_refusals()
        self.addCleanup(fetch.reset_credential_refusals)

    def test_elsevier_api_is_dropped_from_candidates_once_exhausted(self):
        env = {"ELSEVIER_API_KEY": "k"}
        with patch.dict(fetch.os.environ, env), \
             patch.object(fetch, "_get_json", return_value={"message": {"alternative-id": []}}):
            before = fetch._try_publisher_direct("10.1016/j.test.2020.01.001", timeout=5)
            self.assertTrue(any("api.elsevier.com" in url for url, _ in before))

            for _ in range(fetch.CREDENTIAL_GIVE_UP_AFTER):
                fetch.note_credential_refusal("https://api.elsevier.com/content/article/doi/x", "http_403")

            after = fetch._try_publisher_direct("10.1016/j.test.2020.01.002", timeout=5)
        self.assertFalse(any("api.elsevier.com" in url for url, _ in after))

    def test_the_public_sciencedirect_route_is_kept(self):
        """Dropping the API must not drop the routes that need no credential."""
        with patch.dict(fetch.os.environ, {"ELSEVIER_API_KEY": "k"}), \
             patch.object(fetch, "_get_json",
                          return_value={"message": {"alternative-id": ["S0887899416304106"]}}):
            for _ in range(fetch.CREDENTIAL_GIVE_UP_AFTER):
                fetch.note_credential_refusal("https://api.elsevier.com/x", "http_403")
            candidates = fetch._try_publisher_direct("10.1016/j.test.2020.01.003", timeout=5)
        urls = [url for url, _ in candidates]
        self.assertTrue(any("sciencedirect.com" in u for u in urls))
        self.assertFalse(any("api.elsevier.com" in u for u in urls))


if __name__ == "__main__":
    unittest.main()
