# Agentic Engineering Data Platform

A portfolio project for IT companies, built step by step while learning professional software and data engineering.

## Business problem

Engineering teams need reliable information about how work moves from pull request creation to closure and merge. Data spread across tools makes delivery bottlenecks difficult to identify. This project aims to turn engineering data into trustworthy metrics and eventually AI-assisted explanations that help teams investigate problems. Metrics should support team improvement rather than rank individuals.

## Completed functionality

- Multi-repository GitHub ingestion defaults to `dbt-labs/dbt-core` and `apache/airflow`, across open and closed PRs, sorted by creation time descending.
- Pagination follows GitHub's next-page links, with 30 PRs per page and a configurable default maximum of 3 pages per repository (up to 90 PRs each).
- New JSON snapshots in `data/raw/github/` preserve original PR objects plus repository, UTC extraction start/end times, source URL, record count, and per-page metadata.
- Pagination metadata records completeness, successful page count, stop reason, and the next or failed page URL. Successful pages survive later request failures; first-page failures produce metadata snapshots with zero records.
- Timeout, HTTP error, rate-limit, malformed response, and file-error handling, with offline mocked tests. Repositories are processed independently.
- Archived weather proof of concept: fetch Turin hourly temperatures and load them into DuckDB without duplicate timestamps. The scripts in `archive/weather_poc/` still use the existing project-level data files.

GitHub ingestion is a snapshot, not complete history. Page-limited snapshots are explicitly incomplete. Even when pagination is exhausted, PRs can change during extraction and this endpoint does not provide every historical PR event. DuckDB currently supports only the weather example, not engineering analytics. No AI agent is implemented yet.

## Run locally

From PowerShell in the project root, using the existing `.venv` with `requests` and `duckdb` installed:

```powershell
.\.venv\Scripts\python.exe .\src\engineering_intelligence\ingest_github.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Configure repositories and the page cap without editing Python:

```powershell
.\.venv\Scripts\python.exe .\src\engineering_intelligence\ingest_github.py --repositories dbt-labs/dbt-core apache/airflow --max-pages 3
```

`--max-pages` accepts a positive integer. A cap of 1 requests only the latest page. Public repositories need no credentials; none are embedded in the code.

The snapshot's `pagination.stop_reason` is `exhausted`, `page_limit`, `failed`, `partial_failure`, or `empty_page`. `pagination.complete` is true only when no next link remains. An empty page without a next link is valid; an empty page with a next link stops as incomplete. `pagination.next_url` records the remaining or failed page, not an automatic resume mechanism.

Rate limits stop the affected repository with retry guidance; remaining repositories are still attempted. The command exits with code 1 for API, response, or save failures (including an ambiguous empty page). Reaching the page cap is expected and exits successfully. File-write failures are reported, but cannot guarantee a saved snapshot.

Pagination divides results into smaller responses. The code uses `requests` to parse GitHub's `Link` header and follows its next URL, rather than constructing page numbers. See [GitHub pagination documentation](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api).

API rate limits restrict request volume. Unauthenticated public requests normally allow 60 requests per hour per IP address, with additional secondary limits. The script reports `Retry-After` or `X-RateLimit-Reset` when available and does not retry automatically. See [GitHub rate-limit documentation](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).

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
