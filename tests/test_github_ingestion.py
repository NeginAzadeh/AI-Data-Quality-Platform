"""Offline tests for multi-repository ingestion and failure metadata."""

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from src.engineering_intelligence.ingest_github import (
    fetch_pull_requests, ingest_repositories, main, save_snapshot,
)


TARGET = "src.engineering_intelligence.ingest_github.requests.get"
REPOSITORY = "dbt-labs/dbt-core"
BASE_URL = f"https://api.github.com/repos/{REPOSITORY}/pulls"


def api_response(records, url=BASE_URL, next_url=None, status=200, headers=None):
    # Use a real Response so tests exercise parsing of the HTTP Link header.
    response = requests.Response()
    response.status_code = status
    response.url = url
    response.headers.update(headers or {})
    if next_url:
        response.headers["Link"] = f'<{next_url}>; rel="next"'
    response._content = json.dumps(records).encode("utf-8")
    return response


class GitHubIngestionTests(unittest.TestCase):
    @patch(TARGET)
    def test_last_allowed_page_can_still_be_complete(self, mock_get):
        mock_get.return_value = api_response([{"number": 1}])
        snapshot = fetch_pull_requests(REPOSITORY, max_pages=1)
        self.assertTrue(snapshot["pagination"]["complete"])
        self.assertEqual(snapshot["pagination"]["stop_reason"], "exhausted")

    @patch(TARGET)
    def test_partial_snapshot_is_saved_and_next_repository_runs(self, mock_get):
        mock_get.side_effect = [
            api_response([{"number": 1}], next_url=BASE_URL + "?page=2"),
            requests.exceptions.Timeout(),
            api_response([]),
        ]
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(StringIO()):
            failed = ingest_repositories([REPOSITORY, "apache/airflow"], Path(folder))
            snapshots = {s["repository"]: s for s in (
                json.loads(p.read_text(encoding="utf-8")) for p in Path(folder).glob("*.json")
            )}
        self.assertTrue(failed)
        self.assertEqual(snapshots[REPOSITORY]["pull_requests"], [{"number": 1}])
        self.assertEqual(snapshots[REPOSITORY]["pagination"]["stop_reason"], "partial_failure")
        self.assertTrue(snapshots["apache/airflow"]["pagination"]["complete"])

    @patch("src.engineering_intelligence.ingest_github.ingest_repositories")
    def test_command_exit_codes_and_configuration(self, mock_ingest):
        for failed, exit_code in [(False, 0), (True, 1)]:
            with self.subTest(failed=failed):
                mock_ingest.return_value = failed
                with patch("sys.argv", ["ingest_github", "--repositories", "apache/airflow", "--max-pages", "2"]):
                    with self.assertRaises(SystemExit) as result:
                        main()
                self.assertEqual(result.exception.code, exit_code)
                self.assertEqual(mock_ingest.call_args.args[0], ["apache/airflow"])
                self.assertEqual(mock_ingest.call_args.args[2], 2)

    @patch(TARGET)
    def test_fetch_and_save_raw_snapshot(self, mock_get):
        raw_prs = [{"number": 101, "state": "open", "merged_at": None}]
        mock_get.return_value = api_response(raw_prs)
        snapshot = fetch_pull_requests(REPOSITORY)
        self.assertIn("state=all", mock_get.call_args.args[0])
        self.assertIn("sort=created", mock_get.call_args.args[0])
        self.assertIn("direction=desc", mock_get.call_args.args[0])
        self.assertIn("per_page=30", mock_get.call_args.args[0])
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 30)
        with tempfile.TemporaryDirectory() as folder:
            path = save_snapshot(snapshot, Path(folder) / "github")
            saved = json.loads(path.read_text(encoding="utf-8"))
            second = save_snapshot(snapshot, Path(folder) / "github")
            self.assertNotEqual(path, second)
            self.assertTrue(path.exists())
        self.assertEqual(saved["pull_requests"], raw_prs)
        self.assertEqual(saved["repository"], REPOSITORY)
        self.assertEqual(saved["record_count"], 1)
        self.assertTrue(saved["extracted_at_utc"].endswith("+00:00"))
        self.assertTrue(saved["pagination"]["complete"])

    @patch(TARGET)
    def test_follows_next_link_without_rebuilding_url(self, mock_get):
        next_url = "https://api.github.com/repositories/53548867/pulls?page=2&per_page=30"
        mock_get.side_effect = [
            api_response([{"number": 2}], next_url=next_url),
            api_response([{"number": 1}], url=next_url),
        ]
        snapshot = fetch_pull_requests(REPOSITORY)
        self.assertEqual(mock_get.call_args_list[1].args[0], next_url)
        self.assertEqual(snapshot["pull_requests"], [{"number": 2}, {"number": 1}])
        self.assertEqual(snapshot["pagination"]["pages_fetched"], 2)
        self.assertEqual(snapshot["pagination"]["stop_reason"], "exhausted")
        self.assertTrue(snapshot["pagination"]["complete"])

    @patch(TARGET)
    def test_stops_at_default_three_page_limit(self, mock_get):
        mock_get.side_effect = [
            api_response([{"number": page}], next_url=f"{BASE_URL}?page={page+1}")
            for page in range(1, 4)
        ]
        snapshot = fetch_pull_requests(REPOSITORY)
        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(snapshot["pagination"]["stop_reason"], "page_limit")
        self.assertFalse(snapshot["pagination"]["complete"])
        self.assertEqual(snapshot["pagination"]["next_url"], BASE_URL + "?page=4")

    @patch(TARGET)
    def test_configurable_page_limit(self, mock_get):
        mock_get.return_value = api_response([{"number": 1}], next_url=BASE_URL + "?page=2")
        snapshot = fetch_pull_requests(REPOSITORY, max_pages=1)
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(snapshot["pagination"]["max_pages"], 1)
        self.assertEqual(snapshot["pagination"]["stop_reason"], "page_limit")

    @patch(TARGET)
    def test_empty_repository_is_complete(self, mock_get):
        mock_get.return_value = api_response([])
        snapshot = fetch_pull_requests(REPOSITORY)
        self.assertEqual(snapshot["record_count"], 0)
        self.assertEqual(snapshot["pagination"]["pages_fetched"], 1)
        self.assertTrue(snapshot["pagination"]["complete"])

    @patch(TARGET)
    def test_empty_page_with_next_link_is_incomplete(self, mock_get):
        mock_get.return_value = api_response([], next_url=BASE_URL + "?page=2")
        snapshot = fetch_pull_requests(REPOSITORY)
        self.assertFalse(snapshot["pagination"]["complete"])
        self.assertEqual(snapshot["pagination"]["stop_reason"], "empty_page")
        self.assertEqual(mock_get.call_count, 1)

    @patch(TARGET)
    def test_partial_failure_preserves_first_page(self, mock_get):
        next_url = BASE_URL + "?page=2"
        mock_get.side_effect = [
            api_response([{"number": 1}], next_url=next_url),
            requests.exceptions.Timeout(),
        ]
        snapshot = fetch_pull_requests(REPOSITORY)
        self.assertEqual(snapshot["pull_requests"], [{"number": 1}])
        self.assertEqual(snapshot["record_count"], 1)
        self.assertEqual(snapshot["pagination"]["pages_fetched"], 1)
        self.assertEqual(snapshot["pagination"]["stop_reason"], "partial_failure")
        self.assertEqual(snapshot["pagination"]["next_url"], next_url)
        self.assertIn("timed out", snapshot["error"])

    @patch(TARGET)
    def test_rate_limit_reports_retry_delay(self, mock_get):
        mock_get.return_value = api_response(
            {"message": "rate limit"}, status=429, headers={"Retry-After": "60"}
        )
        snapshot = fetch_pull_requests(REPOSITORY)
        self.assertIn("Retry after 60 seconds", snapshot["error"])
        self.assertEqual(snapshot["pagination"]["stop_reason"], "failed")
        self.assertFalse(snapshot["pagination"]["complete"])

    @patch(TARGET)
    def test_primary_rate_limit_reports_utc_reset(self, mock_get):
        mock_get.return_value = api_response(
            {}, status=403,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "0"},
        )
        snapshot = fetch_pull_requests(REPOSITORY)
        self.assertIn("1970-01-01T00:00:00+00:00", snapshot["error"])

    @patch(TARGET)
    def test_http_error_does_not_load_error_body_as_prs(self, mock_get):
        mock_get.return_value = api_response({"message": "server error"}, status=500)
        snapshot = fetch_pull_requests(REPOSITORY)
        self.assertEqual(snapshot["pull_requests"], [])
        self.assertEqual(snapshot["pagination"]["pages_fetched"], 0)
        self.assertIn("500", snapshot["error"])

    @patch(TARGET)
    def test_invalid_json_response_is_failure(self, mock_get):
        response = api_response([])
        response._content = b"not JSON"
        mock_get.return_value = response
        snapshot = fetch_pull_requests(REPOSITORY)
        self.assertEqual(snapshot["pagination"]["stop_reason"], "failed")
        self.assertEqual(snapshot["record_count"], 0)

    @patch(TARGET)
    def test_non_list_response_is_failure(self, mock_get):
        mock_get.return_value = api_response({"unexpected": "object"})
        snapshot = fetch_pull_requests(REPOSITORY)
        self.assertIn("Expected a JSON list", snapshot["error"])

    @patch(TARGET)
    def test_multiple_repositories_have_separate_snapshots(self, mock_get):
        mock_get.side_effect = [api_response([{"number": 1}]), api_response([{"number": 2}])]
        with tempfile.TemporaryDirectory() as folder:
            failed = ingest_repositories([REPOSITORY, "apache/airflow"], Path(folder))
            snapshots = [json.loads(p.read_text(encoding="utf-8")) for p in Path(folder).glob("*.json")]
        self.assertFalse(failed)
        self.assertEqual({s["repository"] for s in snapshots}, {REPOSITORY, "apache/airflow"})
        self.assertIn("apache/airflow/pulls", mock_get.call_args_list[1].args[0])

    @patch(TARGET)
    def test_one_repository_failure_does_not_stop_next_repository(self, mock_get):
        mock_get.side_effect = [requests.exceptions.Timeout(), api_response([{"number": 2}])]
        with tempfile.TemporaryDirectory() as folder:
            failed = ingest_repositories([REPOSITORY, "apache/airflow"], Path(folder))
            snapshots = {s["repository"]: s for s in (
                json.loads(p.read_text(encoding="utf-8")) for p in Path(folder).glob("*.json")
            )}
        self.assertTrue(failed)
        self.assertEqual(snapshots[REPOSITORY]["pagination"]["stop_reason"], "failed")
        self.assertEqual(snapshots["apache/airflow"]["record_count"], 1)

    @patch("src.engineering_intelligence.ingest_github.save_snapshot")
    @patch(TARGET)
    def test_save_failure_does_not_stop_next_repository(self, mock_get, mock_save):
        mock_get.return_value = api_response([])
        mock_save.side_effect = [OSError("disk error"), Path("second.json")]
        self.assertTrue(ingest_repositories([REPOSITORY, "apache/airflow"], Path("unused")))
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_save.call_count, 2)

    def test_invalid_page_limit_rejected(self):
        with self.assertRaises(ValueError):
            fetch_pull_requests(REPOSITORY, max_pages=0)


if __name__ == "__main__":
    unittest.main()
