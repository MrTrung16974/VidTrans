SELECT name FROM sqlite_master
WHERE type = 'table' AND name IN ('jobs', 'tiktok_publish_attempts');

SELECT status, COUNT(*) AS total
FROM tiktok_publish_attempts
GROUP BY status;
