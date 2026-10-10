"""Small offline tests using Python's built-in unittest tools."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from src.engineering_intelligence.ingest_github import (
    SOURCE_URL,
    fetch_pull_requests,
    save_snapshot,
)


class GitHubIngestionTests(unittest.TestCase):
    @patch("src.engineering_intelligence.ingest_github.requests.get")
    def test_fetch_and_save_raw_snapshot(self, mock_get):
        raw_prs = [
            {"number": 101, "state": "open", "merged_at": None},
            {"number": 102, "state": "closed", "merged_at": "2026-09-01T12:00:00Z"},
        ]
        response = Mock(status_code=200)
        response.url = SOURCE_URL + "?state=all&sort=created&direction=desc&per_page=30&page=1"
        response.json.return_value = raw_prs
        mock_get.return_value = response

        pull_requests, source_url = fetch_pull_requests()
        options = mock_get.call_args.kwargs
        self.assertEqual(mock_get.call_args.args, (SOURCE_URL,))
        self.assertEqual(options["params"], {
            "state": "all", "sort": "created", "direction": "desc",
            "per_page": 30, "page": 1,
        })
        self.assertEqual(options["timeout"], 30)
        response.raise_for_status.assert_called_once()

        with tempfile.TemporaryDirectory() as folder:
            path = save_snapshot(pull_requests, source_url, Path(folder) / "github")
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["pull_requests"], raw_prs)
        self.assertEqual(snapshot["repository"], "dbt-labs/dbt-core")
        self.assertEqual(snapshot["record_count"], 2)
        self.assertEqual(snapshot["source_url"], response.url)
        self.assertTrue(snapshot["extracted_at_utc"].endswith("+00:00"))

    @patch("src.engineering_intelligence.ingest_github.requests.get")
    def test_rate_limit_reports_retry_delay(self, mock_get):
        mock_get.return_value = Mock(
            status_code=429, headers={"Retry-After": "60"}, text="rate limit"
        )
        with self.assertRaisesRegex(RuntimeError, "Retry after 60 seconds"):
            fetch_pull_requests()

    @patch("src.engineering_intelligence.ingest_github.requests.get")
    def test_timeout_is_propagated_to_main_handler(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout()
        with self.assertRaises(requests.exceptions.Timeout):
            fetch_pull_requests()

    @patch("src.engineering_intelligence.ingest_github.requests.get")
    def test_http_error_is_not_treated_as_success(self, mock_get):
        response = Mock(status_code=500)
        response.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
        mock_get.return_value = response
        with self.assertRaises(requests.exceptions.HTTPError):
            fetch_pull_requests()
        response.json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
