"""Provider-neutral function schemas; no model SDK or network dependency."""

REPOSITORY_PATTERN = (
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/"
    r"(?!\.{1,2}$)[A-Za-z0-9_.-]{1,100}$"
)
MAX_PR_NUMBER = 9223372036854775807
MAX_AGE_DAYS = 36500
MAX_RESULTS = 100

REPOSITORY_PROPERTY = {
    "type": "string",
    "pattern": REPOSITORY_PATTERN,
    "description": "Public GitHub owner/repository identifier, not a URL or SQL expression.",
}


def schema(name, description, extra_properties=None):
    properties = {"repository": dict(REPOSITORY_PROPERTY), **(extra_properties or {})}
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TOOL_SCHEMAS = [
    schema("get_repository_kpis",
           "Read existing observed-cohort KPIs with provenance and limitations. "
           "Not complete history or a developer productivity assessment."),
    schema("list_stale_open_prs",
           "List PRs observed open at extraction with age >= min_age_days, "
           "oldest first. Links are references; live GitHub status is not verified.", {
               "min_age_days": {"type": "number", "minimum": 0, "maximum": MAX_AGE_DAYS},
               "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS},
           }),
    schema("get_pr_details",
           "Inspect stored PR fields and observation time. No review activity "
           "or live status is collected by this tool.", {
               "pr_number": {"type": "integer", "minimum": 1, "maximum": MAX_PR_NUMBER},
           }),
    schema("get_data_quality_status",
           "Read extraction freshness, completeness, source errors, and quality "
           "warnings. Freshness uses an explicit 24-hour policy, not evidence of a bottleneck."),
]
