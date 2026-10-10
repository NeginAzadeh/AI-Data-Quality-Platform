"""Fetch bounded, paginated PR snapshots from public GitHub repositories."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import requests


DEFAULT_REPOSITORIES = ("dbt-labs/dbt-core", "apache/airflow")
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2026-03-10",
}
PARAMS = {
    "state": "all", "sort": "created", "direction": "desc",
    "per_page": 30, "page": 1,
}


def check_response(response):
    """Report rate-limit guidance, then check other HTTP errors."""
    if response.status_code in (403, 429):
        retry_after = response.headers.get("Retry-After")
        remaining = response.headers.get("X-RateLimit-Remaining")
        if (response.status_code == 429 or retry_after is not None
                or remaining == "0" or "rate limit" in response.text.lower()):
            if retry_after is not None:
                advice = f"Retry after {retry_after} seconds."
            elif remaining == "0" and response.headers.get("X-RateLimit-Reset"):
                reset = datetime.fromtimestamp(
                    int(response.headers["X-RateLimit-Reset"]), timezone.utc
                ).isoformat()
                advice = f"Retry after {reset} (UTC)."
            else:
                advice = "Wait at least one minute before retrying."
            raise RuntimeError(f"GitHub rate limit reached. {advice}")
    response.raise_for_status()


def fetch_pull_requests(repository, max_pages=3):
    """Keep successful pages even if a later request fails."""
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1.")
    if len(repository.split("/")) != 2 or not all(repository.split("/")):
        raise ValueError("Repository must use owner/name format.")

    source_url = f"https://api.github.com/repos/{repository}/pulls"
    next_url = requests.Request("GET", source_url, params=PARAMS).prepare().url
    snapshot = {
        "repository": repository,
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_url": next_url,
        "record_count": 0,
        "pagination": {
            "max_pages": max_pages,
            "pages_fetched": 0,
            "complete": False,
            "stop_reason": None,
            "next_url": next_url,
        },
        "pages": [],
        "error": None,
        "pull_requests": [],
    }
    pagination = snapshot["pagination"]

    for page_number in range(1, max_pages + 1):
        try:
            response = requests.get(next_url, headers=HEADERS, timeout=30)
            check_response(response)
            records = response.json()
            if not isinstance(records, list):
                raise ValueError("Expected a JSON list of pull requests.")
        except requests.exceptions.Timeout:
            snapshot["error"] = "GitHub request timed out. Please try again."
        except (requests.exceptions.RequestException, RuntimeError, ValueError) as error:
            snapshot["error"] = str(error)

        if snapshot["error"] is not None:
            pagination["stop_reason"] = (
                "partial_failure" if snapshot["pages"] else "failed"
            )
            # next_url still identifies the page that failed.
            break

        snapshot["pull_requests"].extend(records)
        snapshot["pages"].append({
            "page_number": page_number,
            "source_url": response.url,
            "record_count": len(records),
            "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        pagination["pages_fetched"] += 1
        # requests parses GitHub's Link header into response.links.
        next_url = response.links.get("next", {}).get("url")
        pagination["next_url"] = next_url
        if next_url is None:
            pagination["complete"] = True
            pagination["stop_reason"] = "exhausted"
            break
        if not records:
            # An empty page with a next link is ambiguous; don't claim completion.
            pagination["stop_reason"] = "empty_page"
            break
    else:
        pagination["stop_reason"] = "page_limit"

    snapshot["record_count"] = len(snapshot["pull_requests"])
    snapshot["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    return snapshot


def save_snapshot(snapshot, output_dir):
    """Write a new file; never overwrite earlier raw snapshots."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    repository_name = snapshot["repository"].replace("/", "_")
    output_path = output_dir / f"{repository_name}_prs_{timestamp}.json"
    with output_path.open("x", encoding="utf-8") as file:
        json.dump(snapshot, file, indent=2, ensure_ascii=False)
        file.write("\n")
    return output_path


def ingest_repositories(repositories, output_dir, max_pages=3):
    """Process repositories independently and report any failures to the caller."""
    had_failure = False
    for repository in repositories:
        try:
            snapshot = fetch_pull_requests(repository, max_pages)
            output_path = save_snapshot(snapshot, output_dir)
        except (ValueError, OSError) as error:
            print(f"{repository}: ingestion failed: {error}")
            had_failure = True
            continue

        pagination = snapshot["pagination"]
        print(f"{repository}: {snapshot['record_count']} PRs, "
              f"{pagination['pages_fetched']} pages, {pagination['stop_reason']}.")
        print(f"Saved: {output_path}")
        if snapshot["error"]:
            print(f"Error: {snapshot['error']}")
        if snapshot["error"] or pagination["stop_reason"] == "empty_page":
            had_failure = True
    return had_failure


def positive_integer(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repositories", nargs="+", default=DEFAULT_REPOSITORIES,
                        help="Public repositories in owner/name format")
    parser.add_argument("--max-pages", type=positive_integer, default=3,
                        help="Maximum pages per repository (default: 3)")
    args = parser.parse_args()
    project_dir = Path(__file__).resolve().parents[2]
    failed = ingest_repositories(
        args.repositories, project_dir / "data" / "raw" / "github", args.max_pages
    )
    # A page cap is expected; API or file failures produce a nonzero exit code.
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
