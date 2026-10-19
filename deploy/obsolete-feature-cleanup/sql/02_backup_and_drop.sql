BEGIN;
CREATE TABLE IF NOT EXISTS tiktok_publish_attempts_backup_20261019
    (LIKE tiktok_publish_attempts INCLUDING ALL);
INSERT INTO tiktok_publish_attempts_backup_20261019
SELECT * FROM tiktok_publish_attempts
ON CONFLICT (attempt_id) DO NOTHING;
DO $$
DECLARE source_count BIGINT; backup_count BIGINT; migrated_count BIGINT;
BEGIN
    SELECT COUNT(*) INTO source_count FROM tiktok_publish_attempts;
    SELECT COUNT(*) INTO backup_count FROM tiktok_publish_attempts_backup_20261019;
    SELECT COUNT(*) INTO migrated_count FROM social_publish_attempts
      WHERE attempt_id LIKE 'legacy-tiktok-%';
    IF backup_count < source_count OR migrated_count < source_count THEN
        RAISE EXCEPTION 'Refusing DROP: source %, backup %, migrated %', source_count, backup_count, migrated_count;
    END IF;
END $$;
DROP TABLE tiktok_publish_attempts;
COMMIT;
