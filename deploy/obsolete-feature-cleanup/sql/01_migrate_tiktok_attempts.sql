BEGIN;

INSERT INTO social_publish_attempts (
    attempt_id, owner_user_id, connection_id, job_id, platform, account_id,
    mode, status, scheduled_for, idempotency_key, remote_media_id,
    payload_json, status_detail_json, error_message, claimed_at, created_at, updated_at
)
SELECT
    'legacy-tiktok-' || legacy.attempt_id,
    COALESCE(jobs.owner_user_id, '__legacy__'),
    COALESCE(legacy.payload_json::jsonb ->> 'connection_id', ''),
    legacy.job_id,
    'TIKTOK',
    COALESCE(legacy.payload_json::jsonb ->> 'account_id', ''),
    CASE legacy.mode
        WHEN 'SCHEDULE' THEN 'SCHEDULE_LOCAL'
        WHEN 'INBOX_DRAFT' THEN 'DRAFT'
        ELSE 'DIRECT_NOW'
    END,
    CASE legacy.status
        WHEN 'READY' THEN 'READY'
        WHEN 'SCHEDULED_LOCAL' THEN 'SCHEDULED_LOCAL'
        WHEN 'PUBLISH_COMPLETE' THEN 'PUBLISHED'
        WHEN 'SEND_TO_USER_INBOX' THEN 'DRAFT_DELIVERED'
        WHEN 'FAILED' THEN 'FAILED'
        WHEN 'CANCELLED' THEN 'CANCELLED'
        ELSE CASE WHEN legacy.remote_publish_id IS NOT NULL
            THEN 'NEEDS_RECONCILIATION' ELSE 'FAILED' END
    END,
    CASE WHEN legacy.status = 'SCHEDULED_LOCAL' THEN legacy.scheduled_for ELSE NULL END,
    legacy.idempotency_key,
    legacy.remote_publish_id,
    (legacy.payload_json::jsonb - 'status_detail' - 'error')::text,
    COALESCE(legacy.payload_json::jsonb -> 'status_detail', '{}'::jsonb)::text,
    COALESCE(
        NULLIF(legacy.payload_json::jsonb ->> 'error', ''),
        CASE WHEN legacy.status NOT IN (
            'READY','SCHEDULED_LOCAL','PUBLISH_COMPLETE','SEND_TO_USER_INBOX','FAILED','CANCELLED'
        ) AND legacy.remote_publish_id IS NULL
        THEN 'Legacy upload interrupted before a remote ID was recorded' END
    ),
    legacy.claimed_at,
    legacy.created_at,
    legacy.updated_at
FROM tiktok_publish_attempts legacy
LEFT JOIN jobs ON jobs.job_id = legacy.job_id
ON CONFLICT (attempt_id) DO NOTHING;

DO $$
DECLARE source_count BIGINT; migrated_count BIGINT;
BEGIN
    SELECT COUNT(*) INTO source_count FROM tiktok_publish_attempts;
    SELECT COUNT(*) INTO migrated_count
      FROM social_publish_attempts WHERE attempt_id LIKE 'legacy-tiktok-%';
    IF migrated_count < source_count THEN
        RAISE EXCEPTION 'TikTok migration incomplete: source %, migrated %', source_count, migrated_count;
    END IF;
END $$;

COMMIT;
