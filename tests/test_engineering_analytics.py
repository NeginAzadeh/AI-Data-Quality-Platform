"""Known inputs and exact expected metrics, using isolated DuckDB fixtures."""

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import duckdb

from src.engineering_intelligence.load_github import create_tables
from src.engineering_intelligence.report_kpis import (
    build_report, display_report, write_report,
)


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.connection = duckdb.connect(":memory:")
        self.addCleanup(self.connection.close)
        create_tables(self.connection)

    def add_run(self, run_id="run-a", repository="example/repo", extracted_at="2026-10-10T00:00:00Z",
                complete=False, error=None, rejected=0):
        self.connection.execute("""
            INSERT INTO ingestion_runs VALUES (?, ?, ?, NULL, 'https://api.github.com',
                'fixture.json', ?, 1, ?, ?, 0, 0, ?, '[]')
        """, [run_id, repository, extracted_at, complete,
              "exhausted" if complete else "page_limit", error, rejected])

    def add_pr(self, number, state="open", created_at="2026-10-01T00:00:00Z",
               merged_at=None, repository="example/repo", run_id="run-a",
               extracted_at="2026-10-10T00:00:00Z"):
        self.connection.execute("""
            INSERT INTO pull_requests VALUES (?, ?, 'Fixture PR', ?, 'developer',
                ?, ?, ?, ?, ?, ?)
        """, [repository, number, state, created_at, merged_at or extracted_at,
              (merged_at or extracted_at) if state == "closed" else None,
              merged_at, extracted_at, run_id])

    def repository_report(self):
        return build_report(self.connection)["repositories"][0]

    def test_controlled_counts_rate_durations_and_open_ages(self):
        self.add_run()
        # Merge durations: 2, 4, and 18 hours; median 4 and mean 8.
        for number, hour in [(1, 2), (2, 4), (3, 18)]:
            self.add_pr(number, state="closed", created_at="2026-10-01T00:00:00Z",
                        merged_at=f"2026-10-01T{hour:02d}:00:00Z")
        self.add_pr(4, state="closed")
        self.add_pr(5, created_at="2026-10-01T00:00:00Z")  # 9 days old
        self.add_pr(6, created_at="2026-10-03T00:00:00Z")  # exactly 7 days old
        self.add_pr(7, created_at="2026-10-08T00:00:00Z")  # 2 days old
        repository = self.repository_report()
        metrics = repository["metrics"]
        self.assertEqual(metrics["total_observed_prs"], 7)
        self.assertEqual(metrics["open_prs"], 3)
        self.assertEqual(metrics["merged_prs"], 3)
        self.assertEqual(metrics["closed_without_merge_prs"], 1)
        self.assertAlmostEqual(metrics["merge_rate"], 3 / 7)
        self.assertEqual(metrics["median_time_to_merge_hours"], 4)
        self.assertEqual(metrics["average_time_to_merge_hours"], 8)
        self.assertEqual(metrics["merge_duration_sample_count"], 3)
        self.assertEqual(metrics["median_open_age_days"], 7)
        self.assertEqual(metrics["average_open_age_days"], 6)
        self.assertEqual(metrics["open_prs_older_than_7_days"], 1)
        self.assertEqual([pr["open_age_days"] for pr in repository["open_prs"]], [9, 7, 2])

    def test_even_median_and_fractional_merge_duration(self):
        self.add_run()
        self.add_pr(1, state="closed", created_at="2026-10-01T00:00:00Z", merged_at="2026-10-01T00:30:00Z")
        self.add_pr(2, state="closed", created_at="2026-10-01T00:00:00Z", merged_at="2026-10-01T03:30:00Z")
        metrics = self.repository_report()["metrics"]
        self.assertEqual(metrics["median_time_to_merge_hours"], 2)
        self.assertEqual(metrics["average_time_to_merge_hours"], 2)

    def test_each_pr_uses_its_own_extraction_not_latest_run_or_system_time(self):
        self.add_run("old", extracted_at="2026-10-03T00:00:00Z", complete=False)
        self.add_run("new", extracted_at="2026-10-20T00:00:00Z", complete=True)
        self.add_pr(1, run_id="old", extracted_at="2026-10-03T00:00:00Z")
        self.add_pr(2, run_id="new", extracted_at="2026-10-20T00:00:00Z")
        # Report generation time is deliberately unavailable during calculation.
        with patch("src.engineering_intelligence.report_kpis.datetime") as clock:
            clock.now.side_effect = AssertionError("KPI calculation must not use the clock")
            first = build_report(self.connection)
            second = build_report(self.connection)
        self.assertEqual(first, second)
        repository = first["repositories"][0]
        self.assertEqual([pr["open_age_days"] for pr in repository["open_prs"]], [19, 2])
        self.assertEqual(repository["metrics"]["open_prs_older_than_7_days"], 1)
        self.assertFalse(repository["cohort"]["all_observation_runs_pagination_complete"])
        self.assertTrue(repository["cohort"]["latest_ingestion_run"]["pagination_complete"])

    def test_multiple_ingestion_runs_do_not_multiply_pr_counts(self):
        for index in range(3):
            self.add_run(f"run-{index}", extracted_at=f"2026-10-{index+10:02d}T00:00:00Z")
        self.add_pr(1, run_id="run-0")
        self.assertEqual(self.repository_report()["metrics"]["total_observed_prs"], 1)

    def test_metrics_are_separate_per_repository(self):
        self.add_run(repository="example/a")
        self.add_run("run-b", repository="example/b")
        self.add_pr(1, repository="example/a")
        self.add_pr(1, state="closed", merged_at="2026-10-01T01:00:00Z",
                    repository="example/b", run_id="run-b")
        repositories = build_report(self.connection)["repositories"]
        self.assertEqual([r["repository"] for r in repositories], ["example/a", "example/b"])
        self.assertEqual([r["metrics"]["merge_rate"] for r in repositories], [0, 1])
        self.assertEqual([len(r["open_prs"]) for r in repositories], [1, 0])

    def test_repository_without_prs_has_zero_counts_and_unavailable_rates(self):
        self.add_run(error="Source timeout")
        repository = self.repository_report()
        metrics = repository["metrics"]
        self.assertEqual(metrics["total_observed_prs"], 0)
        self.assertEqual(metrics["open_prs"], 0)
        self.assertEqual(metrics["merged_prs"], 0)
        self.assertEqual(metrics["closed_without_merge_prs"], 0)
        self.assertIsNone(metrics["merge_rate"])
        self.assertIsNone(metrics["average_time_to_merge_hours"])
        self.assertIsNone(metrics["median_open_age_days"])
        self.assertIsNone(repository["cohort"]["all_observation_runs_pagination_complete"])
        self.assertEqual(repository["cohort"]["latest_ingestion_run"]["source_error"], "Source timeout")
        self.assertEqual(repository["open_prs"], [])

    def test_empty_warehouse_produces_valid_empty_report(self):
        report = build_report(self.connection)
        self.assertEqual(report["repositories"], [])
        with redirect_stdout(StringIO()) as output:
            display_report(report)
        self.assertIn("No observed repositories", output.getvalue())

    def test_no_merges_gives_zero_rate_but_null_merge_durations(self):
        self.add_run()
        self.add_pr(1)
        self.add_pr(2, state="closed")
        metrics = self.repository_report()["metrics"]
        self.assertEqual(metrics["merge_rate"], 0)
        self.assertIsNone(metrics["median_time_to_merge_hours"])
        self.assertIsNone(metrics["average_time_to_merge_hours"])
        self.assertEqual(metrics["merge_duration_sample_count"], 0)

    def test_no_open_prs_gives_null_open_ages(self):
        self.add_run()
        self.add_pr(1, state="closed")
        repository = self.repository_report()
        self.assertEqual(repository["metrics"]["open_age_sample_count"], 0)
        self.assertIsNone(repository["metrics"]["average_open_age_days"])
        self.assertEqual(repository["metrics"]["open_prs_older_than_7_days"], 0)
        self.assertEqual(repository["open_prs"], [])

    def test_unknown_provenance_is_not_claimed_complete(self):
        self.add_pr(1, run_id="missing")
        repository = self.repository_report()
        self.assertIsNone(repository["cohort"]["all_observation_runs_pagination_complete"])
        self.assertIsNone(repository["cohort"]["latest_ingestion_run"]["run_id"])
        self.assertEqual(repository["metrics"]["total_observed_prs"], 1)

    def test_legacy_completeness_remains_unknown(self):
        self.add_run(complete=None)
        self.add_pr(1)
        repository = self.repository_report()
        self.assertIsNone(repository["cohort"]["all_observation_runs_pagination_complete"])
        self.assertIsNone(repository["cohort"]["latest_ingestion_run"]["pagination_complete"])

    def test_failed_latest_run_does_not_reage_or_remove_previous_prs(self):
        self.add_run("observed", extracted_at="2026-10-03T00:00:00Z")
        self.add_run("failed", extracted_at="2026-10-30T00:00:00Z", error="Timeout", rejected=2)
        self.add_pr(1, run_id="observed", extracted_at="2026-10-03T00:00:00Z")
        repository = self.repository_report()
        self.assertEqual(repository["open_prs"][0]["open_age_days"], 2)
        latest = repository["cohort"]["latest_ingestion_run"]
        self.assertEqual(latest["run_id"], "failed")
        self.assertEqual(latest["source_error"], "Timeout")
        self.assertEqual(latest["rejected_records"], 2)

    def test_negative_and_missing_duration_inputs_are_not_reported_as_zero(self):
        # Relax only this fixture to exercise SQL resilience beyond loader guarantees.
        self.connection.execute("ALTER TABLE pull_requests ALTER COLUMN created_at DROP NOT NULL")
        self.add_run()
        self.add_pr(1, created_at=None)
        self.add_pr(2, created_at="2026-10-11T00:00:00Z")
        self.add_pr(3, state="closed", created_at="2026-10-02T00:00:00Z", merged_at="2026-10-01T00:00:00Z")
        repository = self.repository_report()
        metrics = repository["metrics"]
        self.assertEqual(metrics["open_age_sample_count"], 0)
        self.assertIsNone(metrics["average_open_age_days"])
        self.assertEqual(metrics["merge_duration_sample_count"], 0)
        self.assertIsNone(metrics["average_time_to_merge_hours"])
        self.assertEqual(metrics["merged_prs"], 1)
        self.assertTrue(all(pr["open_age_days"] is None for pr in repository["open_prs"]))

    def test_fractional_age_strict_seven_day_boundary_and_timezone_offset(self):
        self.add_run()
        self.add_pr(1, created_at="2026-10-03T00:00:00Z")
        self.add_pr(2, created_at="2026-10-02T23:59:59Z")
        self.add_pr(3, created_at="2026-10-09T14:00:00+02:00")
        repository = self.repository_report()
        ages = {pr["pr_number"]: pr["open_age_days"] for pr in repository["open_prs"]}
        self.assertEqual(ages[1], 7)
        self.assertGreater(ages[2], 7)
        self.assertEqual(ages[3], 0.5)
        self.assertEqual(repository["metrics"]["open_prs_older_than_7_days"], 1)

    def test_json_export_has_metadata_and_preserves_all_open_ages(self):
        self.add_run()
        self.add_pr(1)
        report = build_report(self.connection)
        with tempfile.TemporaryDirectory() as directory:
            path = write_report(report, Path(directory) / "reports")
            payload = json.loads(path.read_text(encoding="utf-8"))
            second = write_report(report, Path(directory) / "reports")
            self.assertNotEqual(path, second)
            self.assertTrue(path.exists())
        self.assertEqual(payload["repositories"], report["repositories"])
        self.assertTrue(payload["generated_at_utc"].endswith("+00:00"))
        self.assertIn("incomplete", payload["cohort_label"])
        self.assertIn("not developer productivity ranking", payload["intended_use"])
        self.assertEqual(payload["repositories"][0]["open_prs"][0]["open_age_days"], 9)

    def test_read_only_reporting_preserves_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "warehouse.duckdb"
            with duckdb.connect(str(path)) as connection:
                create_tables(connection)
            original = path.read_bytes()
            with duckdb.connect(str(path), read_only=True) as connection:
                report = build_report(connection)
            self.assertEqual(report["repositories"], [])
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
