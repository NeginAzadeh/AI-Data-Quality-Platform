# Project architecture

## Current GitHub pipeline

```text
GitHub REST API (dbt-labs/dbt-core and apache/airflow by default)
  -> src/engineering_intelligence/ingest_github.py
  -> data/raw/github/<timestamped snapshot>.json
  -> load_github.py (latest snapshot selection and Data Quality checks)
  -> data/processed/engineering.duckdb
       ingestion_runs + pull_requests
  -> sql/analytics/ (temporary calculations and read-only queries)
  -> report_kpis.py
  -> Console KPI summary + data/reports/<timestamped KPI report>.json

Mock API responses -> tests/test_github_ingestion.py
```

The module follows GitHub next-page links with up to 30 PRs per page across both states, sorted by creation time descending. The configurable default maximum is 3 pages per repository. Each repository produces a separate snapshot with original PR objects, extraction metadata, per-page URLs and counts, and pagination completeness. Previous single-page snapshots remain unchanged.

`check_response` handles HTTP and rate-limit errors; `fetch_pull_requests` collects bounded pages; `save_snapshot` writes new raw files; `ingest_repositories` isolates repositories; `main` parses command-line configuration and sets the exit code. These boundaries allow offline tests of network and storage failures.

Successful pages survive later API failures. Failure metadata is saved even when no page succeeds. Save failures are reported separately, and remaining repositories are still attempted. Rate limits stop the affected repository with retry guidance, without automatic retries. API or save failures produce a nonzero exit code; reaching the page cap is an expected bounded extraction.

Pagination is complete only when no next link remains. A cap, failure, or empty page with a next link is incomplete. Snapshots are not complete event history: PRs can change between page requests. The warehouse preserves completeness metadata and never deletes PRs simply because they are absent from a later bounded snapshot. There is no scheduler yet.

## Implemented warehouse

`load_github.py` reads existing raw files without calling GitHub or modifying JSON. It selects the newest metadata-valid extraction per repository, validates records, and writes two DuckDB tables. Discovery, validation, schema creation, and loading are separate functions so tests can use temporary files and real isolated databases.

`ingestion_runs` records one row per selected snapshot, identified by its SHA-256 content hash. `pull_requests` uses the composite primary key `(repository, pr_number)` and upserts only observations from a newer extraction. All warehouse timestamps are timezone-aware and normalized to UTC. Each snapshot's run metadata and PR changes commit together, or roll back together if database writes fail.

Rejected records remain available in raw JSON, and their record indices, PR numbers, and reasons are recorded in run metadata. Missing pagination metadata means unknown completeness, not complete data. Invalid files are reported, while remaining valid repositories can load. The loader does not backfill all archived snapshots or maintain PR event history. See [the warehouse guide](warehouse.md) for schema and quality rules.

## Archived weather proof of concept

```text
Open-Meteo -> archive/weather_poc/fetch_weather.py
  -> data/raw/turin_temperature_2026-09-01_to_2026-09-03.json
  -> archive/weather_poc/load_weather.py
  -> data/processed/weather.duckdb (weather_hourly table)
```

Both scripts locate the project root with `Path(__file__).resolve().parents[2]`, preserving access to existing data regardless of the terminal's working directory. The loader checks list lengths and prevents duplicate timestamps using a primary key. This learning example is separate from the active GitHub pipeline.

## Implemented analytics

`sql/analytics/pr_observations.sql` defines a session-only view with one row per stored PR, elapsed merge time, and open age at that PR's own extraction timestamp. `repository_kpis.sql` aggregates by repository before joining the latest ingestion metadata, preventing count inflation from multiple runs. `open_pr_ages.sql` lists every observed open PR's age and its observation time.

`report_kpis.py` uses a read-only connection and a transaction so both queries see the same warehouse state. The temporary view is not persisted. Python formats console output and exports structured JSON; SQL owns the KPI formulas. Report generation time is separate from metric inputs. The warehouse, raw files, and existing ingestion functions remain unchanged.

Both latest ingestion context and contributing observation-run context are retained. Older PRs can remain after later bounded or failed extractions, so this is an observed cohort with potentially mixed extraction times, not a complete or simultaneous view of repository activity. See [the analytics guide](analytics.md) for definitions and SQL learning examples.

## Planned downstream pipeline (not implemented)

```text
Existing warehouse and SQL KPI report
  -> dbt-managed transformations
  -> Dashboard and AI Agent
```

The dashboard would present defined metrics. The AI Agent would explain patterns using validated results and suggest investigations. Neither is implemented.

Airflow would coordinate tasks; Docker would package their runtime. GCP would host cloud resources, Terraform would define infrastructure, and CI/CD would automate checks and later delivery. Specific services, metric definitions, and deployment design remain undecided.

## Folder responsibilities

- `src/engineering_intelligence/`: ingestion, warehouse, and report code.
- `sql/analytics/`: PR calculations and repository KPI queries.
- `tests/`: mocked API tests and isolated DuckDB warehouse/analytics tests.
- `archive/weather_poc/`: preserved and runnable learning example.
- `data/raw/`: original API responses.
- `data/processed/`: local engineering and weather databases.
- `data/reports/`: generated KPI JSON, excluded from version control.
- `docs/`: design documentation.

Data and `.venv/` remain local through `.gitignore`. GitHub ingestion, basic Data Quality checks, the local warehouse, and observed-cohort KPIs are implemented. dbt, AI, dashboards, scheduling, and cloud infrastructure remain planned.
