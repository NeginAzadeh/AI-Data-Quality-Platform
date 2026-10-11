"""Warehouse tests use temporary snapshots and real, isolated DuckDB databases."""

import copy
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock

import duckdb

from src.engineering_intelligence.load_github import (
    create_tables, latest_snapshots, load_snapshot, load_warehouse,
    read_snapshot, utc_timestamp, validate_pull_requests,
)


def valid_pr(number=1, **changes):
    record = {
        "number": number, "title": "Example PR", "state": "open",
        "user": {"login": "developer"},
        "created_at": "2026-09-01T10:00:00Z",
        "updated_at": "2026-09-01T11:00:00Z",
        "closed_at": None, "merged_at": None,
    }
    record.update(changes)
    return record


def snapshot_data(records, repository="dbt-labs/dbt-core", extracted_at="2026-10-01T00:00:00Z"):
    return {
        "repository": repository, "extracted_at_utc": extracted_at,
        "source_url": f"https://api.github.com/repos/{repository}/pulls",
        "record_count": len(records), "pull_requests": records,
        "pagination": {"complete": False, "pages_fetched": 1, "stop_reason": "page_limit"},
        "error": None,
    }


class WarehouseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.raw_dir = self.root / "raw"
        self.raw_dir.mkdir()
        self.database_path = self.root / "processed" / "engineering.duckdb"

    def write_snapshot(self, data, name="snapshot.json"):
        path = self.raw_dir / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def load(self):
        with redirect_stdout(StringIO()):
            return load_warehouse(self.raw_dir, self.database_path)

    def query(self, sql):
        with duckdb.connect(str(self.database_path)) as connection:
            connection.execute("SET TimeZone = 'UTC'")
            return connection.execute(sql).fetchall()

    def test_latest_snapshot_per_repository_uses_metadata_not_filename(self):
        self.write_snapshot(snapshot_data([valid_pr(1)]), "z_newest_filename.json")
        self.write_snapshot(snapshot_data([valid_pr(2)], extracted_at="2026-10-02T00:00:00Z"), "a.json")
        self.write_snapshot(snapshot_data([valid_pr(3)], repository="apache/airflow"), "airflow.json")
        summaries, failed = self.load()
        self.assertFalse(failed)
        self.assertEqual(len(summaries), 2)
        self.assertEqual(self.query("SELECT repository, pr_number FROM pull_requests ORDER BY repository"),
                         [("apache/airflow", 3), ("dbt-labs/dbt-core", 2)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM ingestion_runs"), [(2,)])

    def test_rerun_does_not_duplicate_rows_or_runs_and_preserves_raw_bytes(self):
        path = self.write_snapshot(snapshot_data([valid_pr()]))
        original = path.read_bytes()
        summaries, _ = self.load()
        self.assertEqual(summaries[0]["loaded"], 1)
        summaries, failed = self.load()
        self.assertFalse(failed)
        self.assertEqual(summaries[0]["loaded"], 0)
        self.assertEqual(self.query("SELECT COUNT(*) FROM pull_requests"), [(1,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM ingestion_runs"), [(1,)])
        self.assertEqual(path.read_bytes(), original)

    def test_same_pr_number_in_different_repositories_is_allowed(self):
        self.write_snapshot(snapshot_data([valid_pr()]), "dbt.json")
        self.write_snapshot(snapshot_data([valid_pr()], repository="apache/airflow"), "airflow.json")
        self.load()
        self.assertEqual(self.query("SELECT COUNT(*) FROM pull_requests"), [(2,)])

    def test_newer_snapshot_updates_pr_and_preserves_absent_prs(self):
        self.write_snapshot(snapshot_data([valid_pr(1), valid_pr(2)]), "old.json")
        self.load()
        changed = valid_pr(1, title="Merged", state="closed",
                           updated_at="2026-09-02T12:00:00Z",
                           closed_at="2026-09-02T12:00:00Z", merged_at="2026-09-02T12:00:00Z")
        self.write_snapshot(snapshot_data([changed], extracted_at="2026-10-02T00:00:00Z"), "new.json")
        self.load()
        self.assertEqual(self.query("SELECT pr_number, title, state FROM pull_requests ORDER BY pr_number"),
                         [(1, "Merged", "closed"), (2, "Example PR", "open")])
        self.assertEqual(self.query("SELECT COUNT(*) FROM ingestion_runs"), [(2,)])
        self.assertEqual(self.query("SELECT pagination_complete FROM ingestion_runs"), [(False,), (False,)])

    def test_older_snapshot_cannot_overwrite_newer_observation(self):
        old_path = self.write_snapshot(snapshot_data([valid_pr(title="Older")]), "old.json")
        new_path = self.write_snapshot(snapshot_data([valid_pr(title="Newer")], extracted_at="2026-10-02T00:00:00Z"), "new.json")
        with duckdb.connect(":memory:") as connection:
            create_tables(connection)
            load_snapshot(connection, read_snapshot(new_path))
            summary = load_snapshot(connection, read_snapshot(old_path))
            self.assertEqual(summary["loaded"], 0)
            self.assertEqual(connection.execute("SELECT title FROM pull_requests").fetchall(), [("Newer",)])

    def test_missing_required_fields_are_rejected(self):
        for field in ("number", "title", "state", "user", "created_at", "updated_at"):
            with self.subTest(field=field):
                record = valid_pr()
                del record[field]
                accepted, issues = validate_pull_requests([record])
                self.assertEqual(accepted, [])
                self.assertTrue(issues[0]["reasons"])

    def test_invalid_field_types_are_rejected(self):
        for changes in ({"number": True}, {"number": 0}, {"number": 2**63},
                        {"number": "1"}, {"title": " "}, {"state": "merged"},
                        {"user": None}, {"user": {"login": ""}}):
            with self.subTest(changes=changes):
                accepted, issues = validate_pull_requests([valid_pr(**changes)])
                self.assertEqual(accepted, [])
                self.assertTrue(issues)

    def test_duplicate_identifiers_reject_all_copies_but_keep_unique_record(self):
        data = snapshot_data([valid_pr(1), valid_pr(1, title="Different"), valid_pr(2)])
        self.write_snapshot(data)
        summaries, failed = self.load()
        self.assertTrue(failed)
        self.assertEqual(summaries[0]["rejected"], 2)
        self.assertEqual(self.query("SELECT pr_number FROM pull_requests"), [(2,)])
        self.assertEqual(self.query("SELECT accepted_records, rejected_records FROM ingestion_runs"), [(1, 2)])
        saved_issues = json.loads(self.query("SELECT quality_issues FROM ingestion_runs")[0][0])
        self.assertEqual(len(saved_issues), 2)
        self.assertIn("duplicate PR identifier", saved_issues[0]["reasons"][0])

    def test_invalid_timestamps_are_rejected_including_nullable_fields(self):
        for field in ("created_at", "updated_at", "closed_at", "merged_at"):
            for value in ("not-a-date", "2026-09-01T12:00:00", "2026-02-30T12:00:00Z", 123):
                with self.subTest(field=field, value=value):
                    accepted, issues = validate_pull_requests([valid_pr(**{field: value})])
                    self.assertEqual(accepted, [])
                    self.assertTrue(any("invalid" in reason for reason in issues[0]["reasons"]))

    def test_inconsistent_lifecycle_dates_are_rejected(self):
        cases = [
            {"updated_at": "2026-09-01T09:00:00Z"},
            {"state": "closed", "closed_at": "2026-09-01T09:00:00Z"},
            {"state": "closed", "closed_at": None},
            {"closed_at": "2026-09-01T10:30:00Z"},
            {"state": "closed", "closed_at": "2026-09-01T12:00:00Z"},
            {"state": "closed", "closed_at": "2026-09-01T10:30:00Z", "merged_at": "2026-09-01T10:45:00Z"},
            {"merged_at": "2026-09-01T09:00:00Z"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                accepted, issues = validate_pull_requests([valid_pr(**changes)])
                self.assertEqual(accepted, [])
                self.assertTrue(issues)

    def test_open_closed_and_merged_lifecycles_are_accepted(self):
        records = [valid_pr(1),
                   valid_pr(2, state="closed", closed_at="2026-09-01T11:00:00Z"),
                   valid_pr(3, state="closed", closed_at="2026-09-01T11:00:00Z", merged_at="2026-09-01T11:00:00Z")]
        self.write_snapshot(snapshot_data(records))
        summaries, failed = self.load()
        self.assertFalse(failed)
        self.assertEqual(summaries[0]["accepted"], 3)
        self.assertEqual(self.query("SELECT closed_at, merged_at FROM pull_requests WHERE pr_number=1"), [(None, None)])

    def test_timezone_offsets_are_normalized_to_utc(self):
        self.assertEqual(utc_timestamp("2026-09-01T12:00:00+02:00").hour, 10)
        self.write_snapshot(snapshot_data([valid_pr(created_at="2026-09-01T12:00:00+02:00")]))
        self.load()
        # Inspect the stored SQL value without an optional Python timezone package.
        timestamp = self.query("SELECT CAST(created_at AS VARCHAR) FROM pull_requests")[0][0]
        self.assertEqual(timestamp, "2026-09-01 10:00:00+00")

    def test_failed_extraction_is_stored_with_zero_records(self):
        data = snapshot_data([])
        data["error"] = "API timeout"
        data["pagination"].update(pages_fetched=0, stop_reason="failed")
        self.write_snapshot(data)
        summaries, failed = self.load()
        self.assertTrue(failed)
        self.assertEqual(summaries[0]["loaded"], 0)
        self.assertEqual(self.query("SELECT source_error, record_count, pagination_complete FROM ingestion_runs"),
                         [("API timeout", 0, False)])

    def test_legacy_snapshot_has_unknown_completeness(self):
        data = snapshot_data([valid_pr()])
        del data["pagination"]
        self.write_snapshot(data)
        self.load()
        self.assertEqual(self.query("SELECT pagination_complete, pages_fetched FROM ingestion_runs"), [(None, None)])

    def test_record_count_mismatch_is_reported_but_valid_prs_load(self):
        data = snapshot_data([valid_pr()])
        data["record_count"] = 99
        self.write_snapshot(data)
        summaries, failed = self.load()
        self.assertTrue(failed)
        self.assertEqual(summaries[0]["loaded"], 1)
        self.assertEqual(summaries[0]["rejected"], 0)
        self.assertEqual(self.query("SELECT record_count FROM ingestion_runs"), [(1,)])

    def test_contradictory_completeness_is_downgraded_and_reported(self):
        data = snapshot_data([valid_pr()])
        data["pagination"]["complete"] = True
        self.write_snapshot(data)
        summaries, failed = self.load()
        self.assertTrue(failed)
        self.assertFalse(summaries[0]["complete"])
        self.assertEqual(self.query("SELECT pagination_complete FROM ingestion_runs"), [(False,)])

    def test_malformed_snapshot_does_not_stop_other_repositories(self):
        (self.raw_dir / "bad.json").write_text("not JSON", encoding="utf-8")
        self.write_snapshot(snapshot_data([valid_pr()]))
        summaries, failed = self.load()
        self.assertTrue(failed)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(self.query("SELECT COUNT(*) FROM pull_requests"), [(1,)])

    def test_invalid_metadata_is_reported(self):
        original = snapshot_data([valid_pr()])
        for changes in ({"repository": None}, {"extracted_at_utc": "bad"},
                        {"pull_requests": {}}, {"pagination": {"complete": "false"}}):
            with self.subTest(changes=changes):
                data = copy.deepcopy(original)
                data.update(changes)
                path = self.write_snapshot(data)
                with self.assertRaises(ValueError):
                    read_snapshot(path)

    def test_empty_input_does_not_create_database(self):
        summaries, failed = self.load()
        self.assertTrue(failed)
        self.assertEqual(summaries, [])
        self.assertFalse(self.database_path.exists())

    def test_all_invalid_records_still_store_run_metadata(self):
        self.write_snapshot(snapshot_data([None, {}, valid_pr(title="")]))
        summaries, failed = self.load()
        self.assertTrue(failed)
        self.assertEqual(summaries[0]["rejected"], 3)
        self.assertEqual(self.query("SELECT COUNT(*) FROM pull_requests"), [(0,)])
        self.assertEqual(self.query("SELECT rejected_records FROM ingestion_runs"), [(3,)])

    def test_pr_failure_rolls_back_entire_snapshot_transaction(self):
        path = self.write_snapshot(snapshot_data([valid_pr(1), valid_pr(2)]))
        with duckdb.connect(":memory:") as connection:
            create_tables(connection)
            execute = connection.execute
            count = 0
            def fail_on_second_pr(sql, *args):
                nonlocal count
                if "INSERT INTO pull_requests" in sql:
                    count += 1
                    if count == 2:
                        raise duckdb.Error("simulated write failure")
                return execute(sql, *args)
            wrapper = Mock()
            wrapper.execute.side_effect = fail_on_second_pr
            with self.assertRaises(duckdb.Error):
                load_snapshot(wrapper, read_snapshot(path))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM pull_requests").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
