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
- Engineering warehouse in `src/engineering_intelligence/load_github.py` selects the latest metadata-valid snapshot per repository by embedded UTC extraction time and loads `data/processed/engineering.duckdb`.
- `ingestion_runs` stores snapshot provenance, pagination completeness, extraction errors, and quality results. `pull_requests` stores validated PR fields and the newest extracted observation per repository/PR number.
- Data Quality checks reject missing required fields, duplicate identifiers within a snapshot, invalid timestamps, and inconsistent lifecycle dates. Loading is transactional per snapshot and safe to rerun without duplicate PRs or runs.
- SQL analytics under `sql/analytics/` calculates repository-level observed PR counts, state breakdowns, merge rate, average/median time to merge, and open PR ages at each PR's extraction time, including counts older than 7 days.
- `report_kpis.py` reads the warehouse without modifying it, displays KPIs, and exports timestamped JSON under `data/reports/` with per-PR open ages, units/definitions, and ingestion completeness/error context. Controlled DuckDB tests check known results and edge cases.
- Four read-only agent data tools retrieve repository KPIs, bounded lists of observed stale open PRs with GitHub links, stored PR details, and data-quality/freshness status. Tool responses include observation timestamps, provenance, and limitations.
- Provider-neutral function schemas and an allowlisted dispatcher validate arguments and reject arbitrary SQL, unknown tool names, and unexpected fields. No LLM is connected; the tools use the existing local DuckDB dependency and make no API calls.
- Archived weather proof of concept: fetch Turin hourly temperatures and load them into DuckDB without duplicate timestamps. The scripts in `archive/weather_poc/` still use the existing project-level data files.

GitHub ingestion is a snapshot, not complete history. Page-limited snapshots are explicitly incomplete, and the warehouse preserves that status. Even when pagination is exhausted, PRs can change during extraction and this endpoint does not provide every historical PR event. The warehouse stores current observed PR values, not every change over time. KPIs describe this observed, potentially incomplete cohort; they are not full-repository performance statistics or developer productivity rankings. No AI agent is implemented yet.

## Run locally

From PowerShell in the project root, using the existing `.venv` with `requests` and `duckdb` installed:

```powershell
.\.venv\Scripts\python.exe .\src\engineering_intelligence\ingest_github.py
.\.venv\Scripts\python.exe .\src\engineering_intelligence\load_github.py
.\.venv\Scripts\python.exe .\src\engineering_intelligence\report_kpis.py
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

The warehouse loader uses existing snapshots and makes no API requests. It prints accepted records, actual rows inserted/updated, rejected records, and quality issues per repository. Rerunning unchanged snapshots normally reports zero rows inserted/updated. Invalid records remain in raw JSON; their reasons are also saved in `ingestion_runs.quality_issues`. Older single-page snapshots without pagination metadata have unknown completeness (`NULL`).

Malformed files or invalid extraction metadata are reported and skipped; valid files can still load, but the command exits with code 1. Selection is among metadata-valid snapshots, so an older valid snapshot may be selected when a newer file is invalid; this is reported as a failure, not silently treated as a clean load. Rejections, metadata quality issues, database failures, and source extraction errors also produce code 1. Incomplete page-limited data by itself is expected and does not fail the loader.

See [the warehouse guide](docs/warehouse.md) for the schema, upserts, quality policies, and SQL examples. See [the analytics guide](docs/analytics.md) for KPI formulas, SQL explanations, JSON structure, and interpretation limits. See [the agent tools guide](docs/agent_tools.md) for function calling, schemas, validation, and evidence handling.

Call a tool locally without a model or network access:

```powershell
.\.venv\Scripts\python.exe -c "import json; from src.engineering_intelligence.agent_tools import AgentTools; print(json.dumps(AgentTools().call_tool('list_stale_open_prs', {'repository': 'dbt-labs/dbt-core', 'min_age_days': 7, 'limit': 5}), indent=2))"
```

The tool list uses an inclusive age threshold (`>=`); the existing KPI for older than 7 days remains strict (`>`). Both use age at each PR's extraction, not age today. Source freshness is a separate checked-at-time measure with a documented 24-hour threshold. An empty result means no matching local observations, not verified absence on GitHub. Existing ingestion, warehouse, analytics, and archived code are preserved. No paid services, model dependencies, or billing requirements are introduced.

The analytics script needs an existing warehouse with both tables. A valid empty warehouse produces an empty report; a repository with a run but no PRs has zero counts and unavailable (`null`) rates/durations. Generation time labels the output file only; it never affects open ages. `data/reports/` is excluded from Git, and reporting does not call GitHub or change raw snapshots, ingestion code, or warehouse tables.

Optional archived weather commands:

```powershell
.\.venv\Scripts\python.exe .\archive\weather_poc\fetch_weather.py
.\.venv\Scripts\python.exe .\archive\weather_poc\load_weather.py
```

Fetching weather again overwrites its raw JSON; loading reuses the database. GitHub ingestion creates a new snapshot each run. Raw and processed data are excluded from Git.

## Planned features (not implemented)

| Component | Intended purpose |
| --- | --- |
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
src/engineering_intelligence/ Ingestion, warehouse, KPI reporter, and agent tools/schemas
sql/analytics/              Shared PR calculations and SQL report queries
tests/                      Ingestion, warehouse, and analytics tests
docs/                       Architecture, warehouse, and analytics guides
data/raw/                   Original API responses (ignored by Git)
data/processed/             Engineering and weather databases (ignored by Git)
data/reports/               Generated KPI JSON reports (ignored by Git)
README.md                   Purpose, status, and run commands
.gitignore                  Local files excluded from Git
```

See [the architecture document](docs/architecture.md) for data flows and design decisions.
