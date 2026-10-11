-- Session-only view: one row per stored PR. The warehouse is never changed.
CREATE OR REPLACE TEMP VIEW analytics_pr_observations AS
SELECT
    p.repository,
    p.pr_number,
    p.title,
    p.state,
    p.created_at,
    p.merged_at,
    p.source_extracted_at,
    p.last_run_id,
    r.pagination_complete AS observation_pagination_complete,
    r.source_error AS observation_source_error,
    CASE
        WHEN p.merged_at IS NOT NULL AND p.merged_at >= p.created_at
        THEN (epoch(p.merged_at) - epoch(p.created_at)) / 3600.0
    END AS time_to_merge_hours,
    CASE
        WHEN p.state = 'open' AND p.source_extracted_at >= p.created_at
        THEN (epoch(p.source_extracted_at) - epoch(p.created_at)) / 86400.0
    END AS open_age_days
FROM pull_requests AS p
LEFT JOIN ingestion_runs AS r
    ON p.last_run_id = r.run_id AND p.repository = r.repository;
