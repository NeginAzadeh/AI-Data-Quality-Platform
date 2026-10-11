"""Validate the latest GitHub snapshots and load a local engineering warehouse."""

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import duckdb


TIME_FIELDS = ("created_at", "updated_at", "closed_at", "merged_at")


def utc_timestamp(value):
    """Require an ISO timestamp with a timezone; never guess a local timezone."""
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("expected an ISO timestamp with a timezone")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def read_snapshot(path):
    raw_bytes = path.read_bytes()
    snapshot = json.loads(raw_bytes)
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be a JSON object")
    repository = snapshot.get("repository")
    if not isinstance(repository, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("repository must use owner/name format")
    if not isinstance(snapshot.get("pull_requests"), list):
        raise ValueError("pull_requests must be a list")
    extracted_at = utc_timestamp(snapshot.get("extracted_at_utc"))
    finished_at = snapshot.get("finished_at_utc")
    if finished_at is not None:
        finished_at = utc_timestamp(finished_at)
        if finished_at < extracted_at:
            raise ValueError("extraction finished before it started")
    if not isinstance(snapshot.get("source_url"), str) or not snapshot["source_url"]:
        raise ValueError("source_url is required")
    pagination = snapshot.get("pagination", {})
    if not isinstance(pagination, dict):
        raise ValueError("pagination must be an object")
    complete = pagination.get("complete")
    if complete is not None and not isinstance(complete, bool):
        raise ValueError("pagination.complete must be boolean or null")
    pages = pagination.get("pages_fetched")
    if pages is not None and (type(pages) is not int or pages < 0):
        raise ValueError("pages_fetched must be a nonnegative integer")
    stop_reason = pagination.get("stop_reason")
    if stop_reason is not None and not isinstance(stop_reason, str):
        raise ValueError("stop_reason must be text or null")
    error = snapshot.get("error")
    if error is not None and not isinstance(error, str):
        raise ValueError("error must be text or null")
    # Legacy single-page files have no pagination metadata: completeness is unknown.
    return {
        "data": snapshot,
        "path": path,
        "run_id": hashlib.sha256(raw_bytes).hexdigest(),
        "repository": repository.lower(),
        "extracted_at": extracted_at,
        "finished_at": finished_at,
        "complete": complete,
        "pages_fetched": pages,
        "stop_reason": stop_reason,
    }


def latest_snapshots(raw_dir):
    """Choose by embedded UTC extraction time, not file modification time."""
    latest = {}
    errors = []
    for path in sorted(raw_dir.glob("*.json")):
        try:
            snapshot = read_snapshot(path)
        except (OSError, ValueError, OverflowError) as error:
            errors.append(f"{path.name}: {error}")
            continue
        repository = snapshot["repository"]
        previous = latest.get(repository)
        if previous is None or snapshot["extracted_at"] > previous["extracted_at"]:
            latest[repository] = snapshot
    return latest, errors


def validate_pull_requests(records):
    """Return accepted rows and issue details; reject all duplicate identifiers."""
    numbers = Counter(
        record.get("number") for record in records
        if isinstance(record, dict) and type(record.get("number")) is int
    )
    accepted = []
    issues = []
    for index, record in enumerate(records):
        reasons = []
        if not isinstance(record, dict):
            issues.append({"record_index": index, "pr_number": None,
                           "reasons": ["record must be an object"]})
            continue
        number = record.get("number")
        if type(number) is not int or not 0 < number <= 9223372036854775807:
            reasons.append("number must be a positive BIGINT integer")
        elif numbers[number] > 1:
            reasons.append("duplicate PR identifier in snapshot")
        title = record.get("title")
        if not isinstance(title, str) or not title.strip():
            reasons.append("title is required")
        state = record.get("state")
        if state not in ("open", "closed"):
            reasons.append("state must be open or closed")
        user = record.get("user")
        author = user.get("login") if isinstance(user, dict) else None
        if not isinstance(author, str) or not author.strip():
            reasons.append("user.login is required")

        times = {}
        for field in TIME_FIELDS:
            value = record.get(field)
            if value is None:
                times[field] = None
                if field in ("created_at", "updated_at"):
                    reasons.append(f"{field} is required")
                continue
            try:
                times[field] = utc_timestamp(value)
            except (ValueError, OverflowError):
                times[field] = None
                reasons.append(f"{field} is an invalid or timezone-free timestamp")

        created, updated, closed, merged = (times[field] for field in TIME_FIELDS)
        for field in ("updated_at", "closed_at", "merged_at"):
            if created and times[field] and times[field] < created:
                reasons.append(f"{field} is before created_at")
        for field in ("closed_at", "merged_at"):
            if updated and times[field] and times[field] > updated:
                reasons.append(f"{field} is after updated_at")
        if merged and closed and merged > closed:
            reasons.append("merged_at is after closed_at")
        if state == "open" and (record.get("closed_at") is not None or record.get("merged_at") is not None):
            reasons.append("open PR must not have closing or merging dates")
        if state == "closed" and record.get("closed_at") is None:
            reasons.append("closed PR requires closed_at")

        if reasons:
            issues.append({"record_index": index, "pr_number": number, "reasons": reasons})
        else:
            accepted.append((number, title, state, author, created, updated, closed, merged))
    return accepted, issues


def create_tables(connection):
    connection.execute("SET TimeZone = 'UTC'")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_runs (
            run_id VARCHAR PRIMARY KEY,
            repository VARCHAR NOT NULL,
            extracted_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ,
            source_url VARCHAR NOT NULL,
            snapshot_file VARCHAR NOT NULL,
            pagination_complete BOOLEAN,
            pages_fetched INTEGER,
            stop_reason VARCHAR,
            source_error VARCHAR,
            record_count INTEGER NOT NULL,
            accepted_records INTEGER NOT NULL,
            rejected_records INTEGER NOT NULL,
            quality_issues VARCHAR NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS pull_requests (
            repository VARCHAR NOT NULL,
            pr_number BIGINT NOT NULL,
            title VARCHAR NOT NULL,
            state VARCHAR NOT NULL CHECK (state IN ('open', 'closed')),
            author VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            closed_at TIMESTAMPTZ,
            merged_at TIMESTAMPTZ,
            source_extracted_at TIMESTAMPTZ NOT NULL,
            last_run_id VARCHAR NOT NULL,
            PRIMARY KEY (repository, pr_number)
        )
    """)


def load_snapshot(connection, snapshot):
    data = snapshot["data"]
    records = data["pull_requests"]
    accepted, issues = validate_pull_requests(records)
    rejected_count = len(records) - len(accepted)
    if type(data.get("record_count")) is not int or data["record_count"] != len(records):
        issues.append({"record_index": None, "pr_number": None,
                       "reasons": ["metadata record_count does not match actual records"]})
    if snapshot["complete"] is True and (
        data.get("error") or snapshot["stop_reason"] in ("failed", "partial_failure", "page_limit", "empty_page")
    ):
        # Preserve a warning rather than upgrading contradictory metadata to complete.
        issues.append({"record_index": None, "pr_number": None,
                       "reasons": ["complete flag contradicts extraction status"]})
        complete = False
    else:
        complete = snapshot["complete"]

    rows_written = 0
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute("""
            INSERT INTO ingestion_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id) DO NOTHING
        """, [snapshot["run_id"], snapshot["repository"], snapshot["extracted_at"],
              snapshot["finished_at"], data["source_url"], str(snapshot["path"]),
              complete, snapshot["pages_fetched"], snapshot["stop_reason"], data.get("error"),
              len(records), len(accepted), rejected_count, json.dumps(issues)])
        for record in accepted:
            written = connection.execute("""
                INSERT INTO pull_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (repository, pr_number) DO UPDATE SET
                    title = excluded.title,
                    state = excluded.state,
                    author = excluded.author,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    closed_at = excluded.closed_at,
                    merged_at = excluded.merged_at,
                    source_extracted_at = excluded.source_extracted_at,
                    last_run_id = excluded.last_run_id
                WHERE excluded.source_extracted_at > pull_requests.source_extracted_at
                RETURNING pr_number
            """, [snapshot["repository"], *record, snapshot["extracted_at"], snapshot["run_id"]]).fetchall()
            rows_written += len(written)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return {
        "repository": snapshot["repository"], "accepted": len(accepted),
        "loaded": rows_written, "rejected": rejected_count, "issues": issues,
        "complete": complete, "source_error": data.get("error"),
    }


def load_warehouse(raw_dir, database_path):
    snapshots, file_errors = latest_snapshots(raw_dir)
    for error in file_errors:
        print(f"Snapshot error: {error}")
    if not snapshots:
        print("No readable, metadata-valid GitHub snapshots found. Database not created.")
        return [], True

    database_path.parent.mkdir(parents=True, exist_ok=True)
    summaries = []
    had_issues = bool(file_errors)
    with duckdb.connect(str(database_path)) as connection:
        create_tables(connection)
        for repository, snapshot in sorted(snapshots.items()):
            try:
                summary = load_snapshot(connection, snapshot)
            except duckdb.Error as error:
                print(f"{repository}: database load failed: {error}")
                had_issues = True
                continue
            summaries.append(summary)
            issue_count = sum(len(issue["reasons"]) for issue in summary["issues"])
            print(f"{repository}: accepted={summary['accepted']}, loaded/upserted={summary['loaded']}, "
                  f"rejected={summary['rejected']}, quality issues={issue_count}, "
                  f"pagination complete={summary['complete']}")
            for issue in summary["issues"]:
                print(f"  Record {issue['record_index']}, PR {issue['pr_number']}: "
                      + "; ".join(issue["reasons"]))
            if summary["source_error"]:
                print(f"  Source extraction error: {summary['source_error']}")
            had_issues = had_issues or bool(summary["issues"]) or bool(summary["source_error"])
    print(f"Database: {database_path}")
    print("Snapshots provide observed PRs, not complete repository history.")
    return summaries, had_issues


def main():
    project_dir = Path(__file__).resolve().parents[2]
    try:
        _, had_issues = load_warehouse(
            project_dir / "data" / "raw" / "github",
            project_dir / "data" / "processed" / "engineering.duckdb",
        )
    except (OSError, duckdb.Error) as error:
        raise SystemExit(f"Warehouse load failed: {error}")
    raise SystemExit(1 if had_issues else 0)


if __name__ == "__main__":
    main()
