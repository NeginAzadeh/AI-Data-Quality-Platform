# Project architecture

## Current GitHub pipeline

```text
GitHub REST API (dbt-labs/dbt-core)
  -> src/engineering_intelligence/ingest_github.py
  -> data/raw/github/<timestamped snapshot>.json

Mock API responses -> tests/test_github_ingestion.py
```

The module fetches page 1 with up to 30 PRs across both states, sorted by creation time descending. It saves original PR objects with extraction metadata. This captures a snapshot, not complete GitHub history.

Fetching and saving are separate functions so network behavior can be tested offline. The command-line entry point coordinates them and reports errors. Rate limits stop execution with retry guidance. The GitHub flow has no database or scheduler yet.

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

Data and `.venv/` remain local through `.gitignore`. This reorganization adds documentation, not pipeline functionality.
