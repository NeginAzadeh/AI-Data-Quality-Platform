# AI-Powered Engineering Intelligence Platform

A portfolio project for IT companies, built step by step while learning professional software and data engineering.

## Business problem

Engineering teams need reliable information about how work moves from pull request creation to closure and merge. Data spread across tools makes delivery bottlenecks difficult to identify. This project aims to turn engineering data into trustworthy metrics and eventually AI-assisted explanations that help teams investigate problems. Metrics should support team improvement rather than rank individuals.

## Completed functionality

- GitHub ingestion in `src/engineering_intelligence/ingest_github.py` retrieves one page of up to 30 PRs from `dbt-labs/dbt-core`, across open and closed states, sorted by creation time descending.
- Timestamped JSON snapshots in `data/raw/github/` preserve the returned PR objects plus repository, UTC extraction timestamp, source URL, and record count.
- Timeout, HTTP error, and rate-limit handling, with offline mocked API tests.
- Archived weather proof of concept: fetch Turin hourly temperatures and load them into DuckDB without duplicate timestamps. The scripts in `archive/weather_poc/` still use the existing project-level data files.

GitHub ingestion is a snapshot, not complete history. DuckDB currently supports only the weather example, not engineering analytics.

## Run locally

From PowerShell in the project root, using the existing `.venv` with `requests` and `duckdb` installed:

```powershell
.\.venv\Scripts\python.exe .\src\engineering_intelligence\ingest_github.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Optional archived weather commands:

```powershell
.\.venv\Scripts\python.exe .\archive\weather_poc\fetch_weather.py
.\.venv\Scripts\python.exe .\archive\weather_poc\load_weather.py
```

Fetching weather again overwrites its raw JSON; loading reuses the database. GitHub ingestion creates a new snapshot each run. Raw and processed data are excluded from Git.

## Planned features (not implemented)

| Component | Intended purpose |
| --- | --- |
| DuckDB | Store and query engineering data locally |
| Data Quality | Check required fields, duplicates, and timestamp consistency |
| Engineering KPIs | Calculate clearly defined time-to-merge and PR throughput metrics |
| Dashboard | Present engineering metrics and trends |
| AI Agent | Explain patterns and suggest investigations using validated data |
| GCP | Host cloud storage and pipeline workloads |
| Airflow | Schedule and coordinate pipeline tasks |
| dbt | Build documented and tested SQL transformations |
| Docker | Package consistent runtime environments |
| Terraform | Define cloud infrastructure as code |
| CI/CD | Automate checks and later delivery of approved changes |

This is a learning roadmap, not an implemented production system. Exact cloud services and deployment choices remain undecided.

## Project layout

```text
archive/weather_poc/          Preserved, runnable weather scripts
src/engineering_intelligence/ Active GitHub ingestion module
tests/                      Offline ingestion tests
docs/architecture.md        Current design and planned evolution
data/raw/                   Original API responses (ignored by Git)
data/processed/             Existing weather database (ignored by Git)
README.md                   Purpose, status, and run commands
.gitignore                  Local files excluded from Git
```

See [the architecture document](docs/architecture.md) for data flows and design decisions.
