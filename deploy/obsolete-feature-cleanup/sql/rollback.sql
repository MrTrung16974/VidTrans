BEGIN;
CREATE TABLE tiktok_publish_attempts
    (LIKE tiktok_publish_attempts_backup_20261019 INCLUDING ALL);
INSERT INTO tiktok_publish_attempts
SELECT * FROM tiktok_publish_attempts_backup_20261019;
COMMIT;
