"""Report repository KPIs for the observed, potentially incomplete PR cohort."""

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb


PROJECT_DIR = Path(__file__).resolve().parents[2]
SQL_DIR = PROJECT_DIR / "sql" / "analytics"
COHORT_LABEL = "Observed, potentially incomplete PR cohort; not complete repository history"
METRIC_NAMES = (
    "total_observed_prs", "open_prs", "merged_prs", "closed_without_merge_prs",
    "merge_rate", "average_time_to_merge_hours", "median_time_to_merge_hours",
    "merge_duration_sample_count", "average_open_age_days", "median_open_age_days",
    "open_age_sample_count", "open_prs_older_than_7_days",
)


def query_rows(connection, filename):
    result = connection.execute((SQL_DIR / filename).read_text(encoding="utf-8"))
    columns = [column[0] for column in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def build_report(connection):
    """Run SQL in one transaction so both queries see the same warehouse state."""
    connection.execute("SET TimeZone = 'UTC'")
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute((SQL_DIR / "pr_observations.sql").read_text(encoding="utf-8"))
        kpis = query_rows(connection, "repository_kpis.sql")
        open_prs = query_rows(connection, "open_pr_ages.sql")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    repositories = []
    for row in kpis:
        repository = row["repository"]
        raw_issues = row["latest_run_quality_issues"]
        repositories.append({
            "repository": repository,
            "cohort": {
                "label": COHORT_LABEL,
                "earliest_pr_extraction_at_utc": row["earliest_pr_extraction_at_utc"],
                "latest_pr_extraction_at_utc": row["latest_pr_extraction_at_utc"],
                "observation_run_ids": row["observation_run_ids"] or [],
                "all_observation_runs_pagination_complete": row["all_observation_runs_pagination_complete"],
                "prs_from_runs_with_source_errors": row["prs_from_runs_with_source_errors"],
                "latest_ingestion_run": {
                    "run_id": row["latest_run_id"],
                    "extracted_at_utc": row["latest_run_extracted_at_utc"],
                    "pagination_complete": row["latest_run_pagination_complete"],
                    "stop_reason": row["latest_run_stop_reason"],
                    "source_error": row["latest_run_source_error"],
                    "accepted_records": row["latest_run_accepted_records"],
                    "rejected_records": row["latest_run_rejected_records"],
                    "quality_issues": json.loads(raw_issues) if raw_issues else [],
                },
            },
            "metrics": {name: row[name] for name in METRIC_NAMES},
            "open_prs": [pr for pr in open_prs if pr["repository"] == repository],
        })
    return {
        "report_version": 1,
        "cohort_label": COHORT_LABEL,
        "intended_use": "Repository-level delivery investigation, not developer productivity ranking",
        "metric_definitions": {
            "merge_rate": "Merged observed PRs / all observed PRs; fraction from 0 to 1, null if empty",
            "time_to_merge_hours": "Elapsed hours from creation to merge for valid merged PRs only",
            "open_age_days": "Elapsed days from creation to each open PR's own source_extracted_at",
            "open_prs_older_than_7_days": "Open PRs with a valid age strictly greater than 7 elapsed days",
            "null_values": "Unavailable metric, not zero; invalid or missing duration inputs are excluded",
        },
        "repositories": repositories,
    }


def write_report(report, output_dir):
    """Generation time names the artifact; it does not enter any KPI calculation."""
    generated_at = datetime.now(timezone.utc)
    payload = {**report, "generated_at_utc": generated_at.isoformat()}
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"engineering_kpis_{generated_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    with output_path.open("x", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False, allow_nan=False)
        file.write("\n")
    return output_path


def display_report(report):
    print(COHORT_LABEL)
    print("Use for repository-level investigation, not developer productivity ranking.")
    if not report["repositories"]:
        print("No observed repositories or PRs in the warehouse.")
        return

    def number(value, unit):
        return "N/A" if value is None else f"{value:.2f} {unit}"

    for repository in report["repositories"]:
        metrics = repository["metrics"]
        cohort = repository["cohort"]
        latest = cohort["latest_ingestion_run"]
        rate = metrics["merge_rate"]
        print(f"\n{repository['repository']}")
        print(f"  Observed PRs: {metrics['total_observed_prs']} | open: {metrics['open_prs']} | "
              f"merged: {metrics['merged_prs']} | closed without merge: {metrics['closed_without_merge_prs']}")
        print("  Merge rate: " + ("N/A" if rate is None else f"{rate:.2%}"))
        print(f"  Time to merge: median {number(metrics['median_time_to_merge_hours'], 'hours')}, "
              f"average {number(metrics['average_time_to_merge_hours'], 'hours')} "
              f"(n={metrics['merge_duration_sample_count']})")
        print(f"  Open age at extraction: median {number(metrics['median_open_age_days'], 'days')}, "
              f"average {number(metrics['average_open_age_days'], 'days')} "
              f"(n={metrics['open_age_sample_count']})")
        print(f"  Open PRs older than 7 days: {metrics['open_prs_older_than_7_days']}")
        print(f"  Observation times: {cohort['earliest_pr_extraction_at_utc']} "
              f"to {cohort['latest_pr_extraction_at_utc']}")
        print(f"  Latest run: {latest['extracted_at_utc']} | "
              f"pagination complete: {latest['pagination_complete']} | stop: {latest['stop_reason']}")
        print(f"  All contributing PR runs pagination complete: "
              f"{cohort['all_observation_runs_pagination_complete']}")
        if latest["source_error"] or latest["rejected_records"] or latest["quality_issues"]:
            print(f"  Latest run quality: rejected={latest['rejected_records']}, "
                  f"source error={latest['source_error']}, issues={latest['quality_issues']}")
        if repository["open_prs"]:
            print("  Oldest observed open PRs (up to 5; all ages are in JSON):")
            for pr in repository["open_prs"][:5]:
                print(f"    #{pr['pr_number']}: {number(pr['open_age_days'], 'days')} "
                      f"as of {pr['extracted_at_utc']}")


def main():
    database_path = PROJECT_DIR / "data" / "processed" / "engineering.duckdb"
    if not database_path.is_file():
        raise SystemExit("Engineering database not found. Run load_github.py first.")
    try:
        with duckdb.connect(str(database_path), read_only=True) as connection:
            report = build_report(connection)
        output_path = write_report(report, PROJECT_DIR / "data" / "reports")
    except (OSError, duckdb.Error, ValueError) as error:
        raise SystemExit(f"Analytics failed: {error}")
    display_report(report)
    print(f"\nJSON report: {output_path}")


if __name__ == "__main__":
    main()
