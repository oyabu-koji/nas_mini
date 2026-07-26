ALTER TABLE assets
ADD COLUMN preview_generation INTEGER NOT NULL DEFAULT 0
    CHECK (preview_generation >= 0);

ALTER TABLE assets
ADD COLUMN formal_preview_id TEXT
    REFERENCES processed_results(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE assets
ADD COLUMN log_detection_status TEXT NOT NULL DEFAULT 'not_evaluated'
    CHECK (log_detection_status IN ('not_evaluated', 'apple_log', 'not_log', 'unknown'));

ALTER TABLE assets
ADD COLUMN source_profile TEXT
    CHECK (source_profile IS NULL OR length(source_profile) BETWEEN 1 AND 128);

ALTER TABLE assets
ADD COLUMN detector_rule_version TEXT
    CHECK (detector_rule_version IS NULL OR length(detector_rule_version) BETWEEN 1 AND 64);

ALTER TABLE assets
ADD COLUMN detector_manifest_sha256 TEXT
    CHECK (
        detector_manifest_sha256 IS NULL
        OR (
            length(detector_manifest_sha256) = 64
            AND detector_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    );

ALTER TABLE assets
ADD COLUMN detector_evidence_sha256 TEXT
    CHECK (
        detector_evidence_sha256 IS NULL
        OR (
            length(detector_evidence_sha256) = 64
            AND detector_evidence_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    );

ALTER TABLE jobs
ADD COLUMN preview_generation INTEGER
    CHECK (preview_generation IS NULL OR preview_generation >= 0);

CREATE TABLE phase2b_schema_metadata (
    version TEXT PRIMARY KEY NOT NULL,
    schema_sql_sha256 TEXT NOT NULL
        CHECK (
            length(schema_sql_sha256) = 64
            AND schema_sql_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE formal_preview_attempts (
    id TEXT PRIMARY KEY NOT NULL
        CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
    asset_id INTEGER NOT NULL,
    job_id INTEGER NOT NULL UNIQUE,
    preview_generation INTEGER NOT NULL CHECK (preview_generation >= 1),
    state TEXT NOT NULL CHECK (
        state IN (
            'queued', 'probing', 'resolving', 'rendering', 'finalizing',
            'ready', 'failed', 'superseded'
        )
    ),
    detection_status TEXT CHECK (
        detection_status IS NULL
        OR detection_status IN ('apple_log', 'not_log', 'unknown')
    ),
    source_profile TEXT
        CHECK (source_profile IS NULL OR length(source_profile) BETWEEN 1 AND 128),
    detector_rule_version TEXT
        CHECK (detector_rule_version IS NULL OR length(detector_rule_version) BETWEEN 1 AND 64),
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
    detector_evidence_json BLOB
        CHECK (detector_evidence_json IS NULL OR length(detector_evidence_json) <= 4096),
    requested_preset_id TEXT,
    registry_classification TEXT CHECK (
        registry_classification IS NULL
        OR registry_classification IN ('absent', 'disabled', 'registered_invalid', 'valid')
    ),
    applied_preset_id TEXT,
    preset_display_name TEXT,
    preset_kind TEXT,
    preset_version TEXT,
    source_reference TEXT,
    terms_reference TEXT,
    target_color_space TEXT,
    manifest_canonical_bytes BLOB
        CHECK (manifest_canonical_bytes IS NULL OR length(manifest_canonical_bytes) <= 65536),
    manifest_sha256 TEXT
        CHECK (
            manifest_sha256 IS NULL
            OR (length(manifest_sha256) = 64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*')
        ),
    expected_lut_sha256 TEXT
        CHECK (
            expected_lut_sha256 IS NULL
            OR (
                length(expected_lut_sha256) = 64
                AND expected_lut_sha256 NOT GLOB '*[^0-9a-f]*'
            )
        ),
    file_format TEXT CHECK (file_format IS NULL OR file_format = 'cube'),
    grid_size INTEGER CHECK (grid_size IS NULL OR grid_size IN (17, 33, 65)),
    source_root_kind TEXT CHECK (
        source_root_kind IS NULL OR source_root_kind IN ('built_in', 'custom')
    ),
    source_relative_lut_path TEXT,
    transform_kind TEXT CHECK (transform_kind IS NULL OR transform_kind IN ('none', 'lut')),
    color_transform_status TEXT CHECK (
        color_transform_status IS NULL
        OR color_transform_status IN ('not_requested', 'unavailable', 'applied', 'failed')
    ),
    color_transform_error_code TEXT,
    failure_code TEXT,
    result_id TEXT UNIQUE,
    terminal_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (asset_id, preview_generation),
    CHECK (
        (
            detection_status IS NULL
            AND detector_rule_version IS NULL
            AND detector_manifest_sha256 IS NULL
            AND detector_evidence_sha256 IS NULL
            AND detector_evidence_json IS NULL
        )
        OR (
            detection_status IS NOT NULL
            AND detector_rule_version IS NOT NULL
            AND detector_manifest_sha256 IS NOT NULL
            AND detector_evidence_sha256 IS NOT NULL
            AND detector_evidence_json IS NOT NULL
        )
    ),
    CHECK (
        (state NOT IN ('ready', 'failed', 'superseded') AND terminal_at IS NULL)
        OR (state = 'ready' AND result_id IS NOT NULL AND failure_code IS NULL AND terminal_at IS NOT NULL)
        OR (state = 'failed' AND result_id IS NULL AND failure_code IS NOT NULL AND terminal_at IS NOT NULL)
        OR (state = 'superseded' AND result_id IS NULL AND terminal_at IS NOT NULL)
    ),
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE RESTRICT,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE RESTRICT,
    FOREIGN KEY (result_id) REFERENCES processed_results(id) ON DELETE RESTRICT
);

CREATE TABLE preview_provenance (
    id TEXT PRIMARY KEY NOT NULL
        CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
    attempt_id TEXT NOT NULL UNIQUE,
    asset_id INTEGER NOT NULL,
    preview_generation INTEGER NOT NULL CHECK (preview_generation >= 1),
    result_id TEXT NOT NULL UNIQUE,
    derived_file_id INTEGER NOT NULL UNIQUE,
    detection_status TEXT NOT NULL
        CHECK (detection_status IN ('apple_log', 'not_log', 'unknown')),
    source_profile TEXT
        CHECK (source_profile IS NULL OR length(source_profile) BETWEEN 1 AND 128),
    detector_rule_version TEXT NOT NULL
        CHECK (length(detector_rule_version) BETWEEN 1 AND 64),
    detector_manifest_sha256 TEXT NOT NULL
        CHECK (
            length(detector_manifest_sha256) = 64
            AND detector_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    detector_evidence_sha256 TEXT NOT NULL
        CHECK (
            length(detector_evidence_sha256) = 64
            AND detector_evidence_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    requested_preset_id TEXT NOT NULL,
    applied_preset_id TEXT NOT NULL,
    preset_display_name TEXT,
    preset_kind TEXT,
    preset_version TEXT,
    manifest_sha256 TEXT
        CHECK (
            manifest_sha256 IS NULL
            OR (length(manifest_sha256) = 64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*')
        ),
    lut_sha256 TEXT
        CHECK (
            lut_sha256 IS NULL
            OR (length(lut_sha256) = 64 AND lut_sha256 NOT GLOB '*[^0-9a-f]*')
        ),
    transform_kind TEXT NOT NULL CHECK (transform_kind IN ('none', 'lut')),
    color_transform_status TEXT NOT NULL
        CHECK (color_transform_status IN ('not_requested', 'unavailable', 'applied')),
    color_transform_error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (
            detection_status = 'apple_log'
            AND requested_preset_id = 'generated-apple-log-rec709'
            AND applied_preset_id = 'compress-only'
            AND transform_kind = 'none'
            AND color_transform_status = 'unavailable'
            AND color_transform_error_code = 'lut_preset_unavailable'
            AND manifest_sha256 IS NULL
            AND lut_sha256 IS NULL
        )
        OR (
            detection_status IN ('not_log', 'unknown')
            AND requested_preset_id = 'compress-only'
            AND applied_preset_id = 'compress-only'
            AND transform_kind = 'none'
            AND color_transform_status = 'not_requested'
            AND color_transform_error_code IS NULL
            AND manifest_sha256 IS NULL
            AND lut_sha256 IS NULL
        )
        OR (
            detection_status = 'apple_log'
            AND requested_preset_id = 'generated-apple-log-rec709'
            AND applied_preset_id = 'generated-apple-log-rec709'
            AND transform_kind = 'lut'
            AND color_transform_status = 'applied'
            AND color_transform_error_code IS NULL
            AND manifest_sha256 IS NOT NULL
            AND lut_sha256 IS NOT NULL
        )
    ),
    FOREIGN KEY (attempt_id) REFERENCES formal_preview_attempts(id) ON DELETE RESTRICT,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE RESTRICT,
    FOREIGN KEY (result_id) REFERENCES processed_results(id) ON DELETE RESTRICT,
    FOREIGN KEY (derived_file_id) REFERENCES derived_files(id) ON DELETE RESTRICT
);

CREATE INDEX idx_formal_preview_attempts_asset_generation
ON formal_preview_attempts (asset_id, preview_generation DESC);

CREATE INDEX idx_jobs_preview_generation
ON jobs (asset_id, preview_generation)
WHERE preview_generation IS NOT NULL;

DROP TRIGGER IF EXISTS supersede_replaced_active_processed_result;
DROP TRIGGER IF EXISTS validate_active_processed_result;
DROP TRIGGER IF EXISTS prevent_identity_log_preview_ready;

UPDATE jobs
SET preview_generation = 0
WHERE job_type IN ('preview', 'lut_preview')
  AND asset_id IN (
      SELECT asset_id
      FROM upload_sessions
      WHERE type = 'video' AND asset_id IS NOT NULL
  );

CREATE TRIGGER validate_phase2b_preview_job_insert
BEFORE INSERT ON jobs
WHEN NEW.job_type = 'preview'
 AND EXISTS (
    SELECT 1 FROM upload_sessions
    WHERE upload_sessions.asset_id = NEW.asset_id
      AND upload_sessions.type = 'video'
 )
 AND (
    NEW.preview_generation IS NULL
    OR NEW.preview_generation < 1
    OR NOT EXISTS (
        SELECT 1 FROM assets
        WHERE assets.id = NEW.asset_id
          AND assets.type = 'video'
          AND assets.preview_generation = NEW.preview_generation
    )
 )
BEGIN
    SELECT RAISE(ABORT, 'formal_preview_job_relation_invalid');
END;

CREATE TRIGGER prevent_phase2b_lut_preview_job_insert
BEFORE INSERT ON jobs
WHEN NEW.job_type = 'lut_preview'
 AND EXISTS (
    SELECT 1 FROM upload_sessions
    WHERE upload_sessions.asset_id = NEW.asset_id
      AND upload_sessions.type = 'video'
 )
BEGIN
    SELECT RAISE(ABORT, 'formal_preview_lut_job_not_allowed');
END;

CREATE TRIGGER validate_non_preview_job_generation_insert
BEFORE INSERT ON jobs
WHEN NEW.job_type NOT IN ('preview', 'lut_preview')
 AND NEW.preview_generation IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'non_preview_job_generation_invalid');
END;

CREATE TRIGGER validate_formal_preview_attempt_insert
BEFORE INSERT ON formal_preview_attempts
WHEN NOT EXISTS (
    SELECT 1
    FROM jobs
    JOIN assets ON assets.id = NEW.asset_id
    WHERE jobs.id = NEW.job_id
      AND jobs.asset_id = NEW.asset_id
      AND jobs.job_type = 'preview'
      AND jobs.preview_generation = NEW.preview_generation
      AND assets.type = 'video'
)
BEGIN
    SELECT RAISE(ABORT, 'formal_preview_attempt_relation_invalid');
END;

CREATE TRIGGER prevent_formal_preview_attempt_identity_update
BEFORE UPDATE OF asset_id, job_id, preview_generation ON formal_preview_attempts
BEGIN
    SELECT RAISE(ABORT, 'formal_preview_attempt_identity_immutable');
END;

CREATE TRIGGER prevent_formal_preview_related_job_update
BEFORE UPDATE OF asset_id, job_type, preview_generation ON jobs
WHEN EXISTS (
    SELECT 1 FROM formal_preview_attempts
    WHERE formal_preview_attempts.job_id = OLD.id
)
BEGIN
    SELECT RAISE(ABORT, 'formal_preview_job_identity_immutable');
END;

CREATE TRIGGER validate_preview_provenance_insert
BEFORE INSERT ON preview_provenance
WHEN NOT EXISTS (
    SELECT 1
    FROM formal_preview_attempts
    JOIN processed_results ON processed_results.id = NEW.result_id
    JOIN derived_files ON derived_files.id = NEW.derived_file_id
    WHERE formal_preview_attempts.id = NEW.attempt_id
      AND formal_preview_attempts.asset_id = NEW.asset_id
      AND formal_preview_attempts.preview_generation = NEW.preview_generation
      AND formal_preview_attempts.state = 'ready'
      AND formal_preview_attempts.result_id = NEW.result_id
      AND formal_preview_attempts.detection_status = NEW.detection_status
      AND formal_preview_attempts.source_profile IS NEW.source_profile
      AND formal_preview_attempts.detector_rule_version = NEW.detector_rule_version
      AND formal_preview_attempts.detector_manifest_sha256 = NEW.detector_manifest_sha256
      AND formal_preview_attempts.detector_evidence_sha256 = NEW.detector_evidence_sha256
      AND formal_preview_attempts.requested_preset_id = NEW.requested_preset_id
      AND formal_preview_attempts.applied_preset_id = NEW.applied_preset_id
      AND formal_preview_attempts.manifest_sha256 IS NEW.manifest_sha256
      AND formal_preview_attempts.expected_lut_sha256 IS NEW.lut_sha256
      AND formal_preview_attempts.transform_kind = NEW.transform_kind
      AND formal_preview_attempts.color_transform_status = NEW.color_transform_status
      AND formal_preview_attempts.color_transform_error_code IS NEW.color_transform_error_code
      AND processed_results.asset_id = NEW.asset_id
      AND processed_results.derived_file_id = NEW.derived_file_id
      AND processed_results.preview_generation = NEW.preview_generation
      AND processed_results.status = 'ready'
      AND processed_results.superseded_at IS NULL
      AND derived_files.asset_id = NEW.asset_id
      AND derived_files.kind = 'preview'
      AND derived_files.mime_type LIKE 'video/%'
      AND NOT EXISTS (
          SELECT 1 FROM rendition_provenance
          WHERE rendition_provenance.result_id = NEW.result_id
             OR rendition_provenance.derived_file_id = NEW.derived_file_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'formal_preview_provenance_relation_invalid');
END;

CREATE TRIGGER prevent_current_formal_preview_supersede
BEFORE UPDATE OF status ON processed_results
WHEN OLD.status = 'ready'
 AND NEW.status = 'superseded'
 AND EXISTS (
    SELECT 1 FROM assets
    WHERE assets.formal_preview_id = OLD.id
 )
BEGIN
    SELECT RAISE(ABORT, 'formal_preview_current_cannot_be_superseded');
END;

CREATE TRIGGER prevent_dual_formal_rendition_provenance
BEFORE INSERT ON rendition_provenance
WHEN EXISTS (
    SELECT 1 FROM preview_provenance
    WHERE preview_provenance.result_id = NEW.result_id
       OR preview_provenance.derived_file_id = NEW.derived_file_id
)
BEGIN
    SELECT RAISE(ABORT, 'processed_result_provenance_kind_conflict');
END;

CREATE TRIGGER validate_managed_result_preview_generation
BEFORE INSERT ON rendition_provenance
WHEN NOT EXISTS (
    SELECT 1 FROM preview_provenance
    WHERE preview_provenance.result_id = NEW.result_id
       OR preview_provenance.derived_file_id = NEW.derived_file_id
)
 AND NOT EXISTS (
    SELECT 1
    FROM processed_results
    WHERE processed_results.id = NEW.result_id
      AND processed_results.asset_id = NEW.asset_id
      AND processed_results.derived_file_id = NEW.derived_file_id
      AND processed_results.preview_generation IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'managed_result_preview_generation_invalid');
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

CREATE TRIGGER prevent_terminal_formal_preview_attempt_update
BEFORE UPDATE ON formal_preview_attempts
WHEN OLD.state IN ('ready', 'failed', 'superseded')
BEGIN
    SELECT RAISE(ABORT, 'formal_preview_attempt_terminal_immutable');
END;

CREATE TRIGGER prevent_terminal_formal_preview_attempt_delete
BEFORE DELETE ON formal_preview_attempts
WHEN OLD.state IN ('ready', 'failed', 'superseded')
BEGIN
    SELECT RAISE(ABORT, 'formal_preview_attempt_terminal_delete_not_allowed');
END;

CREATE TRIGGER prevent_preview_provenance_update
BEFORE UPDATE ON preview_provenance
BEGIN
    SELECT RAISE(ABORT, 'preview_provenance_immutable');
END;

CREATE TRIGGER prevent_preview_provenance_delete
BEFORE DELETE ON preview_provenance
BEGIN
    SELECT RAISE(ABORT, 'preview_provenance_delete_not_allowed');
END;
