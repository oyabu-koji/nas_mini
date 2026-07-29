CREATE TABLE phase2c_schema_metadata (
    version TEXT PRIMARY KEY NOT NULL,
    schema_sql_sha256 TEXT NOT NULL
        CHECK (
            length(schema_sql_sha256) = 64
            AND schema_sql_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    assets_table_sql_sha256 TEXT NOT NULL
        CHECK (
            length(assets_table_sql_sha256) = 64
            AND assets_table_sql_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE assets_phase2c_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN ('image', 'video')),
    filename TEXT NOT NULL,
    original_path TEXT,
    size_bytes INTEGER,
    server_sha256 TEXT,
    taken_at TEXT,
    latitude REAL,
    longitude REAL,
    exif_json TEXT,
    is_log INTEGER NOT NULL DEFAULT 0 CHECK (is_log IN (0, 1)),
    transfer_status TEXT NOT NULL DEFAULT 'local_only',
    verification_status TEXT NOT NULL DEFAULT 'not_started',
    preview_status TEXT NOT NULL DEFAULT 'not_started',
    review_status TEXT NOT NULL DEFAULT 'not_reviewed',
    delete_candidate_status TEXT NOT NULL DEFAULT 'not_candidate',
    active_processed_result_id TEXT
        REFERENCES processed_results(id) DEFERRABLE INITIALLY DEFERRED,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    rendition_selection_generation INTEGER NOT NULL DEFAULT 0
        CHECK (rendition_selection_generation >= 0),
    preview_generation INTEGER NOT NULL DEFAULT 0
        CHECK (preview_generation >= 0),
    formal_preview_id TEXT
        REFERENCES processed_results(id) DEFERRABLE INITIALLY DEFERRED,
    log_detection_status TEXT NOT NULL DEFAULT 'not_evaluated'
        CHECK (
            log_detection_status IN (
                'not_evaluated', 'apple_log', 'not_log', 'unknown'
            )
        ),
    source_profile TEXT
        CHECK (source_profile IS NULL OR length(source_profile) BETWEEN 1 AND 128),
    detector_rule_version TEXT
        CHECK (
            detector_rule_version IS NULL
            OR length(detector_rule_version) BETWEEN 1 AND 64
        ),
    detector_manifest_sha256 TEXT
        CHECK (
            detector_manifest_sha256 IS NULL
            OR (
                length(detector_manifest_sha256) = 64
                AND detector_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
            )
        ),
    detector_evidence_sha256 TEXT
        CHECK (
            detector_evidence_sha256 IS NULL
            OR (
                length(detector_evidence_sha256) = 64
                AND detector_evidence_sha256 NOT GLOB '*[^0-9a-f]*'
            )
        ),
    CONSTRAINT ck_assets_delete_candidate_status
        CHECK (
            delete_candidate_status IN (
                'not_candidate', 'safe_to_delete_candidate'
            )
        )
);

INSERT INTO assets_phase2c_new (
    id, type, filename, original_path, size_bytes, server_sha256,
    taken_at, latitude, longitude, exif_json, is_log, transfer_status,
    verification_status, preview_status, review_status,
    delete_candidate_status, active_processed_result_id, created_at,
    updated_at, rendition_selection_generation, preview_generation,
    formal_preview_id, log_detection_status, source_profile,
    detector_rule_version, detector_manifest_sha256,
    detector_evidence_sha256
)
SELECT
    id, type, filename, original_path, size_bytes, server_sha256,
    taken_at, latitude, longitude, exif_json, is_log, transfer_status,
    verification_status, preview_status, review_status,
    delete_candidate_status, active_processed_result_id, created_at,
    updated_at, rendition_selection_generation, preview_generation,
    formal_preview_id, log_detection_status, source_profile,
    detector_rule_version, detector_manifest_sha256,
    detector_evidence_sha256
FROM assets;

ALTER TABLE assets RENAME TO assets_phase2c_old;
ALTER TABLE assets_phase2c_new RENAME TO assets;
DROP TABLE assets_phase2c_old;

CREATE UNIQUE INDEX idx_assets_original_path
ON assets (original_path)
WHERE original_path IS NOT NULL;

CREATE TRIGGER prevent_new_asset_active_processed_result
BEFORE INSERT ON assets
WHEN NEW.active_processed_result_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'new_asset_active_processed_result_must_be_null');
END;

CREATE TRIGGER validate_active_processed_result
BEFORE UPDATE OF active_processed_result_id ON assets
WHEN NEW.active_processed_result_id IS NOT NULL
 AND NEW.active_processed_result_id IS NOT OLD.active_processed_result_id
 AND NOT (
    (
        NEW.active_processed_result_id = NEW.formal_preview_id
        AND EXISTS (
            SELECT 1
            FROM preview_provenance
            JOIN processed_results
              ON processed_results.id = preview_provenance.result_id
            JOIN derived_files
              ON derived_files.id = preview_provenance.derived_file_id
            WHERE preview_provenance.asset_id = NEW.id
              AND preview_provenance.result_id = NEW.active_processed_result_id
              AND preview_provenance.preview_generation = NEW.preview_generation
              AND processed_results.asset_id = NEW.id
              AND processed_results.status = 'ready'
              AND processed_results.preview_generation = NEW.preview_generation
              AND derived_files.asset_id = NEW.id
              AND derived_files.kind = 'preview'
              AND derived_files.mime_type LIKE 'video/%'
        )
    )
    OR EXISTS (
        SELECT 1
        FROM processed_results
        JOIN derived_files
          ON derived_files.id = processed_results.derived_file_id
        JOIN rendition_provenance
          ON rendition_provenance.result_id = processed_results.id
         AND rendition_provenance.derived_file_id = derived_files.id
        JOIN renditions
          ON renditions.id = rendition_provenance.rendition_id
        WHERE processed_results.id = NEW.active_processed_result_id
          AND processed_results.asset_id = NEW.id
          AND processed_results.status = 'ready'
          AND processed_results.preview_generation IS NULL
          AND derived_files.asset_id = NEW.id
          AND derived_files.kind = 'rendition'
          AND derived_files.mime_type LIKE 'video/%'
          AND rendition_provenance.asset_id = NEW.id
          AND renditions.asset_id = NEW.id
          AND renditions.result_id = processed_results.id
          AND renditions.state = 'ready'
          AND renditions.selection_generation <= NEW.rendition_selection_generation
          AND NOT EXISTS (
              SELECT 1 FROM renditions AS newer
              WHERE newer.asset_id = NEW.id
                AND newer.selection_generation > renditions.selection_generation
                AND newer.state IN (
                    'queued', 'validating', 'rendering', 'finalizing', 'ready'
                )
          )
    )
 )
BEGIN
    SELECT RAISE(ABORT, 'active_processed_result_invalid');
END;

CREATE TRIGGER validate_asset_detection_identity_update
BEFORE UPDATE OF
    log_detection_status,
    detector_rule_version,
    detector_manifest_sha256,
    detector_evidence_sha256
ON assets
WHEN NOT (
    (
        NEW.log_detection_status = 'not_evaluated'
        AND NEW.detector_rule_version IS NULL
        AND NEW.detector_manifest_sha256 IS NULL
        AND NEW.detector_evidence_sha256 IS NULL
    )
    OR (
        NEW.log_detection_status IN ('apple_log', 'not_log', 'unknown')
        AND NEW.detector_rule_version IS NOT NULL
        AND NEW.detector_manifest_sha256 IS NOT NULL
        AND NEW.detector_evidence_sha256 IS NOT NULL
    )
)
BEGIN
    SELECT RAISE(ABORT, 'asset_detector_identity_invalid');
END;

CREATE TRIGGER validate_formal_preview_pointer
BEFORE UPDATE OF formal_preview_id ON assets
WHEN NEW.formal_preview_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1
    FROM preview_provenance
    JOIN processed_results
      ON processed_results.id = preview_provenance.result_id
    JOIN derived_files
      ON derived_files.id = preview_provenance.derived_file_id
    WHERE preview_provenance.asset_id = NEW.id
      AND preview_provenance.result_id = NEW.formal_preview_id
      AND preview_provenance.preview_generation = NEW.preview_generation
      AND preview_provenance.detection_status = NEW.log_detection_status
      AND preview_provenance.source_profile IS NEW.source_profile
      AND preview_provenance.detector_rule_version = NEW.detector_rule_version
      AND preview_provenance.detector_manifest_sha256 = NEW.detector_manifest_sha256
      AND preview_provenance.detector_evidence_sha256 = NEW.detector_evidence_sha256
      AND processed_results.asset_id = NEW.id
      AND processed_results.status = 'ready'
      AND processed_results.preview_generation = NEW.preview_generation
      AND derived_files.asset_id = NEW.id
      AND derived_files.kind = 'preview'
      AND derived_files.mime_type LIKE 'video/%'
 )
BEGIN
    SELECT RAISE(ABORT, 'formal_preview_pointer_invalid');
END;

CREATE TRIGGER validate_formal_preview_ready
BEFORE UPDATE OF preview_status ON assets
WHEN NEW.preview_status = 'preview_ready'
 AND EXISTS (
    SELECT 1 FROM upload_sessions
    WHERE upload_sessions.asset_id = NEW.id
      AND upload_sessions.type = 'video'
 )
 AND NOT EXISTS (
    SELECT 1
    FROM preview_provenance
    JOIN processed_results
      ON processed_results.id = preview_provenance.result_id
    WHERE preview_provenance.asset_id = NEW.id
      AND preview_provenance.result_id = NEW.formal_preview_id
      AND preview_provenance.preview_generation = NEW.preview_generation
      AND processed_results.asset_id = NEW.id
      AND processed_results.status = 'ready'
      AND processed_results.preview_generation = NEW.preview_generation
 )
BEGIN
    SELECT RAISE(ABORT, 'formal_preview_relation_invalid');
END;

CREATE TRIGGER supersede_replaced_active_processed_result
AFTER UPDATE OF active_processed_result_id ON assets
WHEN OLD.active_processed_result_id IS NOT NULL
 AND (
    NEW.active_processed_result_id IS NULL
    OR NEW.active_processed_result_id <> OLD.active_processed_result_id
 )
 AND OLD.active_processed_result_id IS NOT NEW.formal_preview_id
BEGIN
    UPDATE processed_results
    SET status = 'superseded',
        superseded_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = OLD.active_processed_result_id
      AND status = 'ready';
END;

CREATE TRIGGER prevent_safe_delete_candidate_asset_insert
BEFORE INSERT ON assets
WHEN NEW.delete_candidate_status = 'safe_to_delete_candidate'
BEGIN
    SELECT RAISE(ABORT, 'safe_delete_candidate_insert_not_allowed');
END;

CREATE TRIGGER enforce_safe_delete_candidate_asset_update
BEFORE UPDATE ON assets
WHEN NEW.delete_candidate_status = 'safe_to_delete_candidate'
 AND NOT (
    NEW.type = 'video'
    AND NEW.verification_status = 'file_verified'
    AND NEW.preview_status = 'preview_ready'
    AND NEW.review_status = 'preview_confirmed'
    AND NEW.preview_generation >= 1
    AND NEW.formal_preview_id IS NOT NULL
    AND (
        SELECT COUNT(*) FROM upload_sessions
        WHERE upload_sessions.asset_id = NEW.id
    ) = 1
    AND EXISTS (
        SELECT 1
        FROM upload_sessions
        WHERE upload_sessions.asset_id = NEW.id
          AND upload_sessions.type = 'video'
          AND upload_sessions.status = 'completed'
          AND upload_sessions.size_bytes = NEW.size_bytes
          AND upload_sessions.expected_file_sha256 = NEW.server_sha256
          AND typeof(upload_sessions.expected_file_sha256) = 'text'
          AND length(upload_sessions.expected_file_sha256) = 64
          AND upload_sessions.expected_file_sha256 NOT GLOB '*[^0-9a-f]*'
          AND typeof(NEW.server_sha256) = 'text'
          AND length(NEW.server_sha256) = 64
          AND NEW.server_sha256 NOT GLOB '*[^0-9a-f]*'
          AND upload_sessions.size_bytes BETWEEN 1 AND 1099511627776
          AND upload_sessions.chunk_size_bytes BETWEEN 1 AND 8388608
          AND (
              (upload_sessions.size_bytes + upload_sessions.chunk_size_bytes - 1)
              / upload_sessions.chunk_size_bytes
          ) BETWEEN 1 AND 131072
          AND (
              SELECT COUNT(*) FROM upload_chunks
              WHERE upload_chunks.session_id = upload_sessions.id
          ) = (
              (upload_sessions.size_bytes + upload_sessions.chunk_size_bytes - 1)
              / upload_sessions.chunk_size_bytes
          )
          AND NOT EXISTS (
              SELECT 1
              FROM upload_chunks
              WHERE upload_chunks.session_id = upload_sessions.id
                AND (
                    typeof(upload_chunks.chunk_index) <> 'integer'
                    OR upload_chunks.status <> 'verified'
                    OR upload_chunks.chunk_index < 0
                    OR upload_chunks.chunk_index >= (
                        (
                            upload_sessions.size_bytes
                            + upload_sessions.chunk_size_bytes - 1
                        ) / upload_sessions.chunk_size_bytes
                    )
                    OR upload_chunks.start_offset <> (
                        upload_chunks.chunk_index
                        * upload_sessions.chunk_size_bytes
                    )
                    OR upload_chunks.end_offset <> min(
                        upload_sessions.size_bytes - 1,
                        (
                            (upload_chunks.chunk_index + 1)
                            * upload_sessions.chunk_size_bytes
                        ) - 1
                    )
                    OR upload_chunks.size_bytes <> (
                        min(
                            upload_sessions.size_bytes - 1,
                            (
                                (upload_chunks.chunk_index + 1)
                                * upload_sessions.chunk_size_bytes
                            ) - 1
                        )
                        - (
                            upload_chunks.chunk_index
                            * upload_sessions.chunk_size_bytes
                        ) + 1
                    )
                )
          )
          AND (
              SELECT COALESCE(SUM(upload_chunks.size_bytes), 0)
              FROM upload_chunks
              WHERE upload_chunks.session_id = upload_sessions.id
          ) = upload_sessions.size_bytes
    )
    AND EXISTS (
        SELECT 1
        FROM processed_results
        JOIN derived_files
          ON derived_files.id = processed_results.derived_file_id
        JOIN preview_provenance
          ON preview_provenance.result_id = processed_results.id
         AND preview_provenance.derived_file_id = derived_files.id
        JOIN formal_preview_attempts
          ON formal_preview_attempts.id = preview_provenance.attempt_id
        WHERE processed_results.id = NEW.formal_preview_id
          AND processed_results.asset_id = NEW.id
          AND processed_results.status = 'ready'
          AND processed_results.preview_generation = NEW.preview_generation
          AND processed_results.mime_type = derived_files.mime_type
          AND processed_results.mime_type LIKE 'video/%'
          AND processed_results.size_bytes = derived_files.size_bytes
          AND processed_results.size_bytes > 0
          AND typeof(processed_results.sha256) = 'text'
          AND length(processed_results.sha256) = 64
          AND processed_results.sha256 NOT GLOB '*[^0-9a-f]*'
          AND processed_results.superseded_at IS NULL
          AND derived_files.asset_id = NEW.id
          AND derived_files.kind = 'preview'
          AND preview_provenance.asset_id = NEW.id
          AND preview_provenance.preview_generation = NEW.preview_generation
          AND preview_provenance.detection_status = NEW.log_detection_status
          AND preview_provenance.source_profile IS NEW.source_profile
          AND preview_provenance.detector_rule_version = NEW.detector_rule_version
          AND preview_provenance.detector_manifest_sha256 = NEW.detector_manifest_sha256
          AND preview_provenance.detector_evidence_sha256 = NEW.detector_evidence_sha256
          AND formal_preview_attempts.asset_id = NEW.id
          AND formal_preview_attempts.preview_generation = NEW.preview_generation
          AND formal_preview_attempts.state = 'ready'
          AND formal_preview_attempts.result_id = processed_results.id
          AND formal_preview_attempts.detection_status = preview_provenance.detection_status
          AND formal_preview_attempts.source_profile IS preview_provenance.source_profile
          AND formal_preview_attempts.detector_rule_version = preview_provenance.detector_rule_version
          AND formal_preview_attempts.detector_manifest_sha256 = preview_provenance.detector_manifest_sha256
          AND formal_preview_attempts.detector_evidence_sha256 = preview_provenance.detector_evidence_sha256
          AND formal_preview_attempts.requested_preset_id = preview_provenance.requested_preset_id
          AND formal_preview_attempts.applied_preset_id = preview_provenance.applied_preset_id
          AND formal_preview_attempts.manifest_sha256 IS preview_provenance.manifest_sha256
          AND formal_preview_attempts.expected_lut_sha256 IS preview_provenance.lut_sha256
          AND formal_preview_attempts.transform_kind = preview_provenance.transform_kind
          AND formal_preview_attempts.color_transform_status = preview_provenance.color_transform_status
          AND formal_preview_attempts.color_transform_error_code IS preview_provenance.color_transform_error_code
          AND NOT EXISTS (
              SELECT 1 FROM rendition_provenance
              WHERE rendition_provenance.result_id = processed_results.id
                 OR rendition_provenance.derived_file_id = derived_files.id
          )
          AND (
              (
                  preview_provenance.detection_status = 'apple_log'
                  AND preview_provenance.requested_preset_id = 'generated-apple-log-rec709'
                  AND preview_provenance.applied_preset_id = 'compress-only'
                  AND preview_provenance.transform_kind = 'none'
                  AND preview_provenance.color_transform_status = 'unavailable'
                  AND preview_provenance.color_transform_error_code = 'lut_preset_unavailable'
                  AND preview_provenance.preset_version IS NULL
                  AND preview_provenance.manifest_sha256 IS NULL
                  AND preview_provenance.lut_sha256 IS NULL
              )
              OR (
                  preview_provenance.detection_status IN ('not_log', 'unknown')
                  AND preview_provenance.requested_preset_id = 'compress-only'
                  AND preview_provenance.applied_preset_id = 'compress-only'
                  AND preview_provenance.transform_kind = 'none'
                  AND preview_provenance.color_transform_status = 'not_requested'
                  AND preview_provenance.color_transform_error_code IS NULL
                  AND preview_provenance.preset_version IS NULL
                  AND preview_provenance.manifest_sha256 IS NULL
                  AND preview_provenance.lut_sha256 IS NULL
              )
              OR (
                  preview_provenance.detection_status = 'apple_log'
                  AND preview_provenance.requested_preset_id = 'generated-apple-log-rec709'
                  AND preview_provenance.applied_preset_id = 'generated-apple-log-rec709'
                  AND preview_provenance.transform_kind = 'lut'
                  AND preview_provenance.color_transform_status = 'applied'
                  AND preview_provenance.color_transform_error_code IS NULL
                  AND typeof(preview_provenance.preset_version) = 'text'
                  AND length(preview_provenance.preset_version) > 0
                  AND typeof(preview_provenance.manifest_sha256) = 'text'
                  AND length(preview_provenance.manifest_sha256) = 64
                  AND preview_provenance.manifest_sha256 NOT GLOB '*[^0-9a-f]*'
                  AND typeof(preview_provenance.lut_sha256) = 'text'
                  AND length(preview_provenance.lut_sha256) = 64
                  AND preview_provenance.lut_sha256 NOT GLOB '*[^0-9a-f]*'
              )
          )
    )
 )
BEGIN
    SELECT RAISE(ABORT, 'safe_delete_candidate_relation_invalid');
END;

CREATE TRIGGER prevent_completed_upload_session_update
BEFORE UPDATE OF
    type, size_bytes, expected_file_sha256, chunk_size_bytes,
    original_relative_path, asset_id, status
ON upload_sessions
WHEN OLD.status = 'completed'
 AND (
    NEW.type IS NOT OLD.type
    OR NEW.size_bytes IS NOT OLD.size_bytes
    OR NEW.expected_file_sha256 IS NOT OLD.expected_file_sha256
    OR NEW.chunk_size_bytes IS NOT OLD.chunk_size_bytes
    OR NEW.original_relative_path IS NOT OLD.original_relative_path
    OR NEW.asset_id IS NOT OLD.asset_id
    OR NEW.status IS NOT OLD.status
 )
BEGIN
    SELECT RAISE(ABORT, 'completed_upload_session_is_immutable');
END;

CREATE TRIGGER prevent_completed_upload_session_delete
BEFORE DELETE ON upload_sessions
WHEN OLD.status = 'completed'
BEGIN
    SELECT RAISE(ABORT, 'completed_upload_session_delete_not_allowed');
END;

CREATE TRIGGER prevent_completed_upload_chunk_insert
BEFORE INSERT ON upload_chunks
WHEN EXISTS (
    SELECT 1 FROM upload_sessions
    WHERE upload_sessions.id = NEW.session_id
      AND upload_sessions.status = 'completed'
)
BEGIN
    SELECT RAISE(ABORT, 'completed_upload_chunk_insert_not_allowed');
END;

CREATE TRIGGER prevent_completed_upload_chunk_update
BEFORE UPDATE ON upload_chunks
WHEN EXISTS (
    SELECT 1 FROM upload_sessions
    WHERE upload_sessions.id IN (OLD.session_id, NEW.session_id)
      AND upload_sessions.status = 'completed'
)
BEGIN
    SELECT RAISE(ABORT, 'completed_upload_chunk_is_immutable');
END;

CREATE TRIGGER prevent_completed_upload_chunk_delete
BEFORE DELETE ON upload_chunks
WHEN EXISTS (
    SELECT 1 FROM upload_sessions
    WHERE upload_sessions.id = OLD.session_id
      AND upload_sessions.status = 'completed'
)
BEGIN
    SELECT RAISE(ABORT, 'completed_upload_chunk_delete_not_allowed');
END;

CREATE TRIGGER prevent_finalized_session_asset_update
BEFORE UPDATE OF
    type, original_path, size_bytes, server_sha256, verification_status
ON assets
WHEN OLD.verification_status = 'file_verified'
 AND EXISTS (
    SELECT 1 FROM upload_sessions
    WHERE upload_sessions.asset_id = OLD.id
      AND upload_sessions.status = 'completed'
 )
 AND (
    NEW.type IS NOT OLD.type
    OR NEW.original_path IS NOT OLD.original_path
    OR NEW.size_bytes IS NOT OLD.size_bytes
    OR NEW.server_sha256 IS NOT OLD.server_sha256
    OR NEW.verification_status IS NOT OLD.verification_status
 )
BEGIN
    SELECT RAISE(ABORT, 'finalized_session_asset_is_immutable');
END;

CREATE TRIGGER prevent_finalized_session_asset_delete
BEFORE DELETE ON assets
WHEN OLD.verification_status = 'file_verified'
 AND EXISTS (
    SELECT 1 FROM upload_sessions
    WHERE upload_sessions.asset_id = OLD.id
      AND upload_sessions.status = 'completed'
 )
BEGIN
    SELECT RAISE(ABORT, 'finalized_session_asset_delete_not_allowed');
END;

CREATE TRIGGER prevent_current_formal_derived_file_update
BEFORE UPDATE OF asset_id, kind, path, mime_type, size_bytes ON derived_files
WHEN EXISTS (
    SELECT 1
    FROM assets
    JOIN processed_results
      ON processed_results.id = assets.formal_preview_id
    JOIN preview_provenance
      ON preview_provenance.result_id = processed_results.id
     AND preview_provenance.derived_file_id = OLD.id
    WHERE processed_results.status = 'ready'
      AND processed_results.preview_generation = assets.preview_generation
)
 AND (
    NEW.asset_id IS NOT OLD.asset_id
    OR NEW.kind IS NOT OLD.kind
    OR NEW.path IS NOT OLD.path
    OR NEW.mime_type IS NOT OLD.mime_type
    OR NEW.size_bytes IS NOT OLD.size_bytes
 )
BEGIN
    SELECT RAISE(ABORT, 'current_formal_derived_file_is_immutable');
END;

CREATE TRIGGER prevent_current_formal_derived_file_delete
BEFORE DELETE ON derived_files
WHEN EXISTS (
    SELECT 1
    FROM assets
    JOIN processed_results
      ON processed_results.id = assets.formal_preview_id
    JOIN preview_provenance
      ON preview_provenance.result_id = processed_results.id
     AND preview_provenance.derived_file_id = OLD.id
    WHERE processed_results.status = 'ready'
      AND processed_results.preview_generation = assets.preview_generation
)
BEGIN
    SELECT RAISE(ABORT, 'current_formal_derived_file_delete_not_allowed');
END;
