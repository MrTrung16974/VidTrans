SELECT to_regclass('tiktok_publish_attempts') AS legacy_table,
       to_regclass('social_publish_attempts') AS target_table;
SELECT status, COUNT(*) FROM tiktok_publish_attempts GROUP BY status ORDER BY status;
SELECT COUNT(*) AS orphan_jobs
FROM tiktok_publish_attempts legacy LEFT JOIN jobs ON jobs.job_id=legacy.job_id
WHERE jobs.job_id IS NULL;
