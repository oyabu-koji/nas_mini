ALTER TABLE assets
ADD COLUMN rendition_selection_generation INTEGER NOT NULL DEFAULT 0
    CHECK (rendition_selection_generation >= 0);

CREATE TABLE renditions (
    id TEXT PRIMARY KEY NOT NULL
        CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
    asset_id INTEGER NOT NULL,
    client_request_id TEXT NOT NULL UNIQUE
        CHECK (length(client_request_id) = 32 AND client_request_id NOT GLOB '*[^0-9a-f]*'),
    job_id INTEGER NOT NULL UNIQUE,
    selection_generation INTEGER NOT NULL CHECK (selection_generation >= 1),
    base_result_id TEXT,
    base_derived_file_id INTEGER,
    base_result_sha256 TEXT CHECK (
        base_result_sha256 IS NULL OR (length(base_result_sha256) = 64 AND base_result_sha256 NOT GLOB '*[^0-9a-f]*')
    ),
    requested_preset_id TEXT NOT NULL
        CHECK (
            length(requested_preset_id) BETWEEN 1 AND 64
            AND requested_preset_id NOT GLOB '*[^a-z0-9-]*'
            AND requested_preset_id NOT LIKE '-%'
            AND requested_preset_id NOT LIKE '%-'
            AND requested_preset_id NOT LIKE '%--%'
        ),
    registry_classification TEXT NOT NULL
        CHECK (registry_classification IN ('absent', 'disabled', 'registered_invalid', 'valid')),
    state TEXT NOT NULL
        CHECK (state IN ('queued', 'validating', 'rendering', 'finalizing', 'ready', 'failed', 'superseded')),
    applied_preset_id TEXT,
    color_transform_status TEXT
        CHECK (color_transform_status IS NULL OR color_transform_status IN ('not_requested', 'unavailable', 'applied', 'failed')),
    error_code TEXT,
    result_id TEXT UNIQUE,
    manifest_canonical_bytes BLOB CHECK (
        manifest_canonical_bytes IS NULL OR length(manifest_canonical_bytes) <= 65536
    ),
    manifest_sha256 TEXT CHECK (
        manifest_sha256 IS NULL OR (length(manifest_sha256) = 64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*')
    ),
    expected_lut_sha256 TEXT CHECK (
        expected_lut_sha256 IS NULL OR (length(expected_lut_sha256) = 64 AND expected_lut_sha256 NOT GLOB '*[^0-9a-f]*')
    ),
    preset_version TEXT CHECK (preset_version IS NULL OR length(preset_version) BETWEEN 1 AND 64),
    source_root_kind TEXT CHECK (source_root_kind IS NULL OR source_root_kind IN ('built_in', 'custom')),
    source_relative_lut_path TEXT,
    preset_display_name TEXT,
    preset_kind TEXT,
    source_reference TEXT,
    terms_reference TEXT,
    target_color_space TEXT,
    file_format TEXT CHECK (file_format IS NULL OR file_format = 'cube'),
    grid_size INTEGER CHECK (grid_size IS NULL OR grid_size IN (17, 33, 65)),
    terminal_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (state IN ('queued', 'validating', 'rendering', 'finalizing')
            AND result_id IS NULL AND applied_preset_id IS NULL
            AND color_transform_status IS NULL AND error_code IS NULL AND terminal_at IS NULL)
        OR (state = 'failed'
            AND result_id IS NULL AND error_code IS NOT NULL AND terminal_at IS NOT NULL
            AND color_transform_status = 'failed')
        OR (state IN ('ready', 'superseded')
            AND result_id IS NOT NULL AND applied_preset_id IS NOT NULL
            AND color_transform_status IN ('not_requested', 'unavailable', 'applied')
            AND terminal_at IS NOT NULL)
    ),
    CHECK (
        (source_root_kind IS NULL
            AND source_relative_lut_path IS NULL
            AND expected_lut_sha256 IS NULL
            AND file_format IS NULL
            AND grid_size IS NULL)
        OR (source_root_kind IS NOT NULL
            AND source_relative_lut_path IS NOT NULL
            AND expected_lut_sha256 IS NOT NULL
            AND file_format = 'cube'
            AND grid_size IS NOT NULL)
    ),
    CHECK (
        (base_result_id IS NULL
            AND base_derived_file_id IS NULL
            AND base_result_sha256 IS NULL)
        OR (base_result_id IS NOT NULL
            AND base_derived_file_id IS NOT NULL
            AND base_result_sha256 IS NOT NULL)
    ),
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE RESTRICT,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE RESTRICT,
    FOREIGN KEY (base_result_id) REFERENCES processed_results(id) ON DELETE RESTRICT,
    FOREIGN KEY (base_derived_file_id) REFERENCES derived_files(id) ON DELETE RESTRICT,
    FOREIGN KEY (result_id) REFERENCES processed_results(id) ON DELETE RESTRICT
);

CREATE TABLE rendition_provenance (
    rendition_id TEXT PRIMARY KEY,
    asset_id INTEGER NOT NULL,
    result_id TEXT NOT NULL UNIQUE,
    derived_file_id INTEGER NOT NULL UNIQUE,
    requested_preset_id TEXT NOT NULL,
    applied_preset_id TEXT NOT NULL,
    preset_version TEXT,
    manifest_sha256 TEXT,
    lut_sha256 TEXT,
    transform_kind TEXT NOT NULL CHECK (transform_kind IN ('none', 'lut')),
    color_transform_status TEXT NOT NULL CHECK (
        color_transform_status IN ('not_requested', 'unavailable', 'applied')
    ),
    color_transform_error_code TEXT,
    preset_kind TEXT,
    source_reference TEXT,
    terms_reference TEXT,
    target_color_space TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (color_transform_status = 'unavailable' AND color_transform_error_code = 'lut_preset_unavailable')
        OR (color_transform_status <> 'unavailable' AND color_transform_error_code IS NULL)
    ),
    CHECK (
        (transform_kind = 'lut' AND color_transform_status = 'applied' AND lut_sha256 IS NOT NULL)
        OR (transform_kind = 'none' AND color_transform_status IN ('not_requested', 'unavailable'))
    ),
    FOREIGN KEY (rendition_id) REFERENCES renditions(id) ON DELETE RESTRICT,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE RESTRICT,
    FOREIGN KEY (result_id) REFERENCES processed_results(id) ON DELETE RESTRICT,
    FOREIGN KEY (derived_file_id) REFERENCES derived_files(id) ON DELETE RESTRICT
);

CREATE INDEX idx_renditions_asset_generation
ON renditions (asset_id, selection_generation DESC);

DROP TRIGGER IF EXISTS prevent_processed_result_derived_file_mismatch_insert;
DROP TRIGGER IF EXISTS prevent_processed_result_derived_file_mismatch_update;
DROP TRIGGER IF EXISTS validate_active_processed_result;

CREATE TRIGGER prevent_processed_result_derived_file_mismatch_insert
BEFORE INSERT ON processed_results
WHEN NEW.derived_file_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1 FROM derived_files
    WHERE id = NEW.derived_file_id
      AND asset_id = NEW.asset_id
      AND kind IN ('preview', 'rendition')
      AND mime_type LIKE 'video/%'
 )
BEGIN
    SELECT RAISE(ABORT, 'processed_result_derived_file_mismatch');
END;

CREATE TRIGGER prevent_processed_result_derived_file_mismatch_update
BEFORE UPDATE OF asset_id, derived_file_id ON processed_results
WHEN NEW.derived_file_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1 FROM derived_files
    WHERE id = NEW.derived_file_id
      AND asset_id = NEW.asset_id
      AND kind IN ('preview', 'rendition')
      AND mime_type LIKE 'video/%'
 )
BEGIN
    SELECT RAISE(ABORT, 'processed_result_derived_file_mismatch');
END;

CREATE TRIGGER validate_active_processed_result
BEFORE UPDATE OF active_processed_result_id ON assets
WHEN NEW.active_processed_result_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1
    FROM processed_results
    JOIN derived_files ON derived_files.id = processed_results.derived_file_id
    WHERE processed_results.id = NEW.active_processed_result_id
      AND processed_results.asset_id = NEW.id
      AND processed_results.status = 'ready'
      AND processed_results.superseded_at IS NULL
      AND derived_files.asset_id = NEW.id
      AND derived_files.kind IN ('preview', 'rendition')
      AND derived_files.mime_type LIKE 'video/%'
      AND (
        derived_files.kind = 'preview'
        OR EXISTS (
            SELECT 1 FROM rendition_provenance
            WHERE rendition_provenance.asset_id = NEW.id
              AND rendition_provenance.result_id = processed_results.id
              AND rendition_provenance.derived_file_id = derived_files.id
        )
      )
 )
BEGIN
    SELECT RAISE(ABORT, 'active_processed_result_invalid');
END;

CREATE TRIGGER validate_rendition_job_relation_insert
BEFORE INSERT ON renditions
WHEN NOT EXISTS (
    SELECT 1 FROM jobs
    WHERE jobs.id = NEW.job_id
      AND jobs.asset_id = NEW.asset_id
      AND jobs.job_type = 'rendition'
)
BEGIN
    SELECT RAISE(ABORT, 'rendition_job_relation_invalid');
END;

CREATE TRIGGER validate_rendition_base_relation_insert
BEFORE INSERT ON renditions
WHEN NEW.base_result_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1
    FROM processed_results
    JOIN derived_files ON derived_files.id = processed_results.derived_file_id
    WHERE processed_results.id = NEW.base_result_id
      AND processed_results.asset_id = NEW.asset_id
      AND processed_results.derived_file_id = NEW.base_derived_file_id
      AND processed_results.sha256 = NEW.base_result_sha256
      AND processed_results.status = 'ready'
      AND processed_results.superseded_at IS NULL
      AND derived_files.id = NEW.base_derived_file_id
      AND derived_files.asset_id = NEW.asset_id
      AND derived_files.kind IN ('preview', 'rendition')
      AND derived_files.mime_type LIKE 'video/%'
 )
BEGIN
    SELECT RAISE(ABORT, 'rendition_base_relation_invalid');
END;

CREATE TRIGGER prevent_rendition_job_relation_update
BEFORE UPDATE OF asset_id, job_id, base_result_id, base_derived_file_id, base_result_sha256 ON renditions
BEGIN
    SELECT RAISE(ABORT, 'rendition_job_relation_immutable');
END;

CREATE TRIGGER prevent_related_job_identity_update
BEFORE UPDATE OF asset_id, job_type ON jobs
WHEN EXISTS (SELECT 1 FROM renditions WHERE renditions.job_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'rendition_job_relation_immutable');
END;

CREATE TRIGGER validate_rendition_provenance_insert
BEFORE INSERT ON rendition_provenance
WHEN NOT EXISTS (
    SELECT 1
    FROM renditions
    JOIN processed_results ON processed_results.id = NEW.result_id
    JOIN derived_files ON derived_files.id = NEW.derived_file_id
    WHERE renditions.id = NEW.rendition_id
      AND renditions.asset_id = NEW.asset_id
      AND processed_results.asset_id = NEW.asset_id
      AND processed_results.derived_file_id = NEW.derived_file_id
      AND processed_results.status IN ('ready', 'superseded')
      AND derived_files.asset_id = NEW.asset_id
      AND derived_files.kind = 'rendition'
      AND derived_files.mime_type LIKE 'video/%'
)
BEGIN
    SELECT RAISE(ABORT, 'rendition_provenance_relation_invalid');
END;

CREATE TRIGGER validate_rendition_terminal_update
BEFORE UPDATE OF state, result_id ON renditions
WHEN NEW.state IN ('ready', 'superseded')
 AND NOT EXISTS (
    SELECT 1 FROM rendition_provenance
    WHERE rendition_id = NEW.id
      AND asset_id = NEW.asset_id
      AND result_id = NEW.result_id
 )
BEGIN
    SELECT RAISE(ABORT, 'rendition_terminal_provenance_missing');
END;

CREATE TRIGGER prevent_terminal_rendition_update
BEFORE UPDATE ON renditions
WHEN OLD.state IN ('ready', 'failed', 'superseded')
BEGIN
    SELECT RAISE(ABORT, 'terminal_rendition_is_immutable');
END;

CREATE TRIGGER prevent_terminal_rendition_delete
BEFORE DELETE ON renditions
WHEN OLD.state IN ('ready', 'failed', 'superseded')
BEGIN
    SELECT RAISE(ABORT, 'terminal_rendition_delete_not_allowed');
END;

CREATE TRIGGER prevent_rendition_provenance_update
BEFORE UPDATE ON rendition_provenance
BEGIN
    SELECT RAISE(ABORT, 'rendition_provenance_is_immutable');
END;

CREATE TRIGGER prevent_rendition_provenance_delete
BEFORE DELETE ON rendition_provenance
BEGIN
    SELECT RAISE(ABORT, 'rendition_provenance_delete_not_allowed');
END;
