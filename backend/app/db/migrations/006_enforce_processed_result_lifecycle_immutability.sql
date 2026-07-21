DROP TRIGGER IF EXISTS prevent_ready_processed_result_update;

CREATE TRIGGER prevent_ready_processed_result_update
BEFORE UPDATE ON processed_results
WHEN OLD.status = 'ready'
 AND (
    NEW.status <> 'superseded'
    OR NEW.id IS NOT OLD.id
    OR NEW.asset_id IS NOT OLD.asset_id
    OR NEW.derived_file_id IS NOT OLD.derived_file_id
    OR NEW.mime_type IS NOT OLD.mime_type
    OR NEW.size_bytes IS NOT OLD.size_bytes
    OR NEW.sha256 IS NOT OLD.sha256
    OR NEW.preview_generation IS NOT OLD.preview_generation
    OR NEW.failure_code IS NOT OLD.failure_code
    OR NEW.created_at IS NOT OLD.created_at
 )
BEGIN
    SELECT RAISE(ABORT, 'processed_result_ready_is_immutable');
END;
