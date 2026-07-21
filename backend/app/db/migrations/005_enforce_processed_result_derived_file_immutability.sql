DROP TRIGGER IF EXISTS prevent_processed_result_derived_file_update;

CREATE TRIGGER prevent_processed_result_derived_file_update
BEFORE UPDATE ON derived_files
WHEN EXISTS (
    SELECT 1
    FROM processed_results
    WHERE derived_file_id = OLD.id
      AND status IN ('ready', 'superseded')
)
BEGIN
    SELECT RAISE(ABORT, 'processed_result_derived_file_is_immutable');
END;
