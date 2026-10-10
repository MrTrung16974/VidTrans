PRAGMA integrity_check;

SELECT name FROM sqlite_master
WHERE type = 'index' AND name LIKE 'idx_tiktok_publish_%';
