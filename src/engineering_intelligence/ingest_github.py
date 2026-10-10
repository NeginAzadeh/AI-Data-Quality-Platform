"""Fetch one page of the latest pull requests from dbt-core."""

import json
from datetime import datetime, timezone
from pathlib import Path

import requests


REPOSITORY = "dbt-labs/dbt-core"
SOURCE_URL = f"https://api.github.com/repos/{REPOSITORY}/pulls"
PARAMS = {
    "state": "all",
    "sort": "created",
    "direction": "desc",
    "per_page": 30,
    "page": 1,
}


def fetch_pull_requests():
    """Return untouched PR objects and the full request URL."""
    response = requests.get(
        SOURCE_URL,
        params=PARAMS,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        },
        timeout=30,
    )

    # GitHub uses 403 or 429 for primary and secondary rate limits.
    if response.status_code in (403, 429):
        retry_after = response.headers.get("Retry-After")
        remaining = response.headers.get("X-RateLimit-Remaining")
        message = response.text.lower()
        if (
            response.status_code == 429
            or retry_after is not None
            or remaining == "0"
            or "rate limit" in message
        ):
            if retry_after is not None:
                advice = f"Retry after {retry_after} seconds."
            elif remaining == "0" and response.headers.get("X-RateLimit-Reset"):
                reset_time = datetime.fromtimestamp(
                    int(response.headers["X-RateLimit-Reset"]), timezone.utc
                ).isoformat()
                advice = f"Retry after {reset_time} (UTC)."
            else:
                advice = "Wait at least one minute before retrying."
            raise RuntimeError(f"GitHub rate limit reached. {advice}")

    response.raise_for_status()
    pull_requests = response.json()
    if not isinstance(pull_requests, list):
        raise ValueError("Expected a JSON list of pull requests.")
    return pull_requests, response.url


def save_snapshot(pull_requests, source_url, output_dir):
    """Save raw PR data together with metadata describing this extraction."""
    extracted_at = datetime.now(timezone.utc)
    snapshot = {
        "repository": REPOSITORY,
        "extracted_at_utc": extracted_at.isoformat(),
        "source_url": source_url,
        "record_count": len(pull_requests),
        "pull_requests": pull_requests,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"dbt-labs_dbt-core_prs_{extracted_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    output_path = output_dir / filename
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(snapshot, file, indent=2, ensure_ascii=False)
        file.write("\n")
    return output_path


def main():
    project_dir = Path(__file__).resolve().parents[2]
    try:
        pull_requests, source_url = fetch_pull_requests()
        output_path = save_snapshot(
            pull_requests, source_url, project_dir / "data" / "raw" / "github"
        )
    except requests.exceptions.Timeout:
        raise SystemExit("GitHub request timed out. Please try again.")
    except requests.exceptions.RequestException as error:
        raise SystemExit(f"GitHub request failed: {error}")
    except (RuntimeError, ValueError, OSError) as error:
        raise SystemExit(f"Ingestion failed: {error}")

    print(f"Fetched {len(pull_requests)} PRs from {REPOSITORY} (page 1 only).")
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
