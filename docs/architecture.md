# Project architecture

## Current GitHub pipeline

```text
GitHub REST API (dbt-labs/dbt-core and apache/airflow by default)
  -> src/engineering_intelligence/ingest_github.py
  -> data/raw/github/<timestamped snapshot>.json

Mock API responses -> tests/test_github_ingestion.py
```

The module follows GitHub next-page links with up to 30 PRs per page across both states, sorted by creation time descending. The configurable default maximum is 3 pages per repository. Each repository produces a separate snapshot with original PR objects, extraction metadata, per-page URLs and counts, and pagination completeness. Previous single-page snapshots remain unchanged.

`check_response` handles HTTP and rate-limit errors; `fetch_pull_requests` collects bounded pages; `save_snapshot` writes new raw files; `ingest_repositories` isolates repositories; `main` parses command-line configuration and sets the exit code. These boundaries allow offline tests of network and storage failures.

Successful pages survive later API failures. Failure metadata is saved even when no page succeeds. Save failures are reported separately, and remaining repositories are still attempted. Rate limits stop the affected repository with retry guidance, without automatic retries. API or save failures produce a nonzero exit code; reaching the page cap is an expected bounded extraction.

Pagination is complete only when no next link remains. A cap, failure, or empty page with a next link is incomplete. Snapshots are not complete event history: PRs can change between page requests. The GitHub flow has no database or scheduler yet.

## Archived weather proof of concept

```text
Open-Meteo -> archive/weather_poc/fetch_weather.py
  -> data/raw/turin_temperature_2026-09-01_to_2026-09-03.json
  -> archive/weather_poc/load_weather.py
  -> data/processed/weather.duckdb (weather_hourly table)
```

Both scripts locate the project root with `Path(__file__).resolve().parents[2]`, preserving access to existing data regardless of the terminal's working directory. The loader checks list lengths and prevents duplicate timestamps using a primary key. This learning example is separate from the active GitHub pipeline.

## Planned pipeline (not implemented)

```text
Engineering source APIs
  -> Raw snapshots
  -> Data Quality checks
  -> DuckDB engineering tables
  -> dbt transformations and Engineering KPIs
  -> Dashboard and AI Agent
```

The dashboard would present defined metrics. The AI Agent would explain patterns using validated results and suggest investigations. Neither is implemented.

Airflow would coordinate tasks; Docker would package their runtime. GCP would host cloud resources, Terraform would define infrastructure, and CI/CD would automate checks and later delivery. Specific services, metric definitions, and deployment design remain undecided.

## Folder responsibilities

- `src/engineering_intelligence/`: active ingestion code.
- `tests/`: automated tests using mocked GitHub responses.
- `archive/weather_poc/`: preserved and runnable learning example.
- `data/raw/`: original API responses.
- `data/processed/`: existing weather database and future generated outputs.
- `docs/`: design documentation.

Data and `.venv/` remain local through `.gitignore`. The active implementation is limited to multi-repository GitHub ingestion; all downstream components remain planned.
