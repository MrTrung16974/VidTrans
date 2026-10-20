SELECT to_regclass('tiktok_publish_attempts') AS expected_null;
SELECT COUNT(*) AS migrated_tiktok_attempts
FROM social_publish_attempts WHERE attempt_id LIKE 'legacy-tiktok-%';
SELECT status, COUNT(*) FROM social_publish_attempts
WHERE platform='TIKTOK' GROUP BY status ORDER BY status;
