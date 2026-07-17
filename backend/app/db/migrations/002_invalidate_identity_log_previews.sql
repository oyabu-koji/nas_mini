CREATE TRIGGER IF NOT EXISTS prevent_identity_log_preview_ready
AFTER UPDATE OF preview_status ON assets
WHEN NEW.is_log = 1 AND NEW.preview_status = 'preview_ready'
BEGIN
    UPDATE assets
    SET preview_status = 'failed',
        review_status = 'not_reviewed',
        updated_at = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;

UPDATE assets
SET preview_status = 'failed',
    review_status = 'not_reviewed',
    updated_at = CURRENT_TIMESTAMP
WHERE is_log = 1
  AND preview_status = 'preview_ready';
