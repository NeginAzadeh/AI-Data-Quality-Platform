-- Each PR is aged at its own observation time, not at the latest repository run.
SELECT
    repository,
    pr_number,
    title,
    strftime(created_at, '%Y-%m-%dT%H:%M:%S.%fZ') AS created_at_utc,
    strftime(source_extracted_at, '%Y-%m-%dT%H:%M:%S.%fZ') AS extracted_at_utc,
    open_age_days,
    CASE WHEN open_age_days IS NOT NULL THEN open_age_days > 7.0 END AS older_than_7_days,
    last_run_id AS observation_run_id
FROM analytics_pr_observations
WHERE state = 'open'
ORDER BY repository, open_age_days DESC NULLS LAST, pr_number;
