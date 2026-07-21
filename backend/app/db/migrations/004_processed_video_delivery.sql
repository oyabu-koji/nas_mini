-- Rebuild the Phase 2A tables so assets can hold a deferred FK to processed_results.
-- Rebuilding the known child tables preserves their FK targets while keeping
-- PRAGMA foreign_keys enabled for the whole migration.
DROP TRIGGER IF EXISTS prevent_identity_log_preview_ready;
DROP INDEX IF EXISTS idx_assets_original_path;
DROP INDEX IF EXISTS idx_jobs_claim;
DROP INDEX IF EXISTS idx_jobs_lease_recovery;
DROP INDEX IF EXISTS idx_jobs_dedup_key;
DROP INDEX IF EXISTS idx_upload_sessions_active;
DROP INDEX IF EXISTS idx_upload_chunks_session;

ALTER TABLE upload_chunks RENAME TO upload_chunks_pre_processed_result;
ALTER TABLE upload_sessions RENAME TO upload_sessions_pre_processed_result;
ALTER TABLE jobs RENAME TO jobs_pre_processed_result;
ALTER TABLE derived_files RENAME TO derived_files_pre_processed_result;
ALTER TABLE assets RENAME TO assets_pre_processed_result;

CREATE TABLE assets (
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
    active_processed_result_id TEXT REFERENCES processed_results(id) DEFERRABLE INITIALLY DEFERRED,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE derived_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    asset_id INTEGER,
    payload_json TEXT,
    error_message TEXT,
    claimed_at TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dedup_key TEXT,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL
);

CREATE TABLE upload_sessions (
    id TEXT PRIMARY KEY,
    client_upload_id TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK (type = 'video'),
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    expected_file_sha256 TEXT NOT NULL,
    chunk_size_bytes INTEGER NOT NULL CHECK (chunk_size_bytes > 0),
    original_relative_path TEXT NOT NULL UNIQUE,
    taken_at TEXT,
    latitude REAL,
    longitude REAL,
    exif_json TEXT,
    is_log INTEGER NOT NULL DEFAULT 0 CHECK (is_log IN (0, 1)),
    status TEXT NOT NULL CHECK (status IN (
        'created', 'uploading', 'ready_to_finalize', 'assembling',
        'completed', 'failed', 'cancelled', 'expired'
    )),
    failure_code TEXT,
    retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0, 1)),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_activity_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    finalization_job_id INTEGER UNIQUE,
    asset_id INTEGER UNIQUE,
    claimed_at TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (finalization_job_id) REFERENCES jobs(id) ON DELETE SET NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL
);

CREATE TABLE upload_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset >= start_offset),
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status = 'verified'),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (session_id, chunk_index),
    FOREIGN KEY (session_id) REFERENCES upload_sessions(id) ON DELETE CASCADE
);

CREATE TABLE processed_results (
    id TEXT PRIMARY KEY NOT NULL
        CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
    asset_id INTEGER NOT NULL,
    derived_file_id INTEGER UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('ready', 'failed', 'superseded')),
    mime_type TEXT,
    size_bytes INTEGER,
    sha256 TEXT,
    preview_generation INTEGER CHECK (preview_generation IS NULL OR preview_generation >= 0),
    failure_code TEXT,
    superseded_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (
            status = 'failed'
            AND derived_file_id IS NULL
            AND mime_type IS NULL
            AND size_bytes IS NULL
            AND sha256 IS NULL
            AND superseded_at IS NULL
        )
        OR (
            status = 'ready'
            AND derived_file_id IS NOT NULL
            AND mime_type LIKE 'video/%'
            AND size_bytes > 0
            AND length(sha256) = 64
            AND sha256 NOT GLOB '*[^0-9a-f]*'
            AND failure_code IS NULL
            AND superseded_at IS NULL
        )
        OR (
            status = 'superseded'
            AND derived_file_id IS NOT NULL
            AND mime_type LIKE 'video/%'
            AND size_bytes > 0
            AND length(sha256) = 64
            AND sha256 NOT GLOB '*[^0-9a-f]*'
            AND failure_code IS NULL
            AND superseded_at IS NOT NULL
        )
    ),
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE RESTRICT,
    FOREIGN KEY (derived_file_id) REFERENCES derived_files(id) ON DELETE RESTRICT
);

INSERT INTO assets (
    id, type, filename, original_path, size_bytes, server_sha256, taken_at,
    latitude, longitude, exif_json, is_log, transfer_status, verification_status,
    preview_status, review_status, delete_candidate_status, created_at, updated_at
)
SELECT
    id, type, filename, original_path, size_bytes, server_sha256, taken_at,
    latitude, longitude, exif_json, is_log, transfer_status, verification_status,
    preview_status, review_status, delete_candidate_status, created_at, updated_at
FROM assets_pre_processed_result;

INSERT INTO derived_files
SELECT * FROM derived_files_pre_processed_result;

INSERT INTO jobs
SELECT * FROM jobs_pre_processed_result;

INSERT INTO upload_sessions
SELECT * FROM upload_sessions_pre_processed_result;

INSERT INTO upload_chunks
SELECT * FROM upload_chunks_pre_processed_result;

DROP TABLE upload_chunks_pre_processed_result;
DROP TABLE upload_sessions_pre_processed_result;
DROP TABLE jobs_pre_processed_result;
DROP TABLE derived_files_pre_processed_result;
DROP TABLE assets_pre_processed_result;

CREATE UNIQUE INDEX idx_assets_original_path
ON assets (original_path)
WHERE original_path IS NOT NULL;

CREATE INDEX idx_jobs_claim
ON jobs (status, created_at);

CREATE INDEX idx_jobs_lease_recovery
ON jobs (status, lease_expires_at);

CREATE UNIQUE INDEX idx_jobs_dedup_key
ON jobs (dedup_key)
WHERE dedup_key IS NOT NULL;

CREATE INDEX idx_upload_sessions_active
ON upload_sessions (status, expires_at);

CREATE INDEX idx_upload_chunks_session
ON upload_chunks (session_id, chunk_index);

CREATE TRIGGER prevent_identity_log_preview_ready
AFTER UPDATE OF preview_status ON assets
WHEN NEW.is_log = 1 AND NEW.preview_status = 'preview_ready'
BEGIN
    UPDATE assets
    SET preview_status = 'failed',
        review_status = 'not_reviewed',
        updated_at = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;

CREATE TRIGGER prevent_processed_result_derived_file_mismatch_insert
BEFORE INSERT ON processed_results
WHEN NEW.derived_file_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1
    FROM derived_files
    WHERE id = NEW.derived_file_id
      AND asset_id = NEW.asset_id
      AND kind = 'preview'
      AND mime_type LIKE 'video/%'
 )
BEGIN
    SELECT RAISE(ABORT, 'processed_result_derived_file_mismatch');
END;

CREATE TRIGGER prevent_processed_result_derived_file_mismatch_update
BEFORE UPDATE OF asset_id, derived_file_id ON processed_results
WHEN NEW.derived_file_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1
    FROM derived_files
    WHERE id = NEW.derived_file_id
      AND asset_id = NEW.asset_id
      AND kind = 'preview'
      AND mime_type LIKE 'video/%'
 )
BEGIN
    SELECT RAISE(ABORT, 'processed_result_derived_file_mismatch');
END;

CREATE TRIGGER prevent_ready_processed_result_update
BEFORE UPDATE ON processed_results
WHEN OLD.status = 'ready'
 AND NEW.status <> 'superseded'
BEGIN
    SELECT RAISE(ABORT, 'processed_result_ready_is_immutable');
END;

CREATE TRIGGER prevent_active_processed_result_supersede
BEFORE UPDATE OF status ON processed_results
WHEN OLD.status = 'ready'
 AND NEW.status = 'superseded'
 AND EXISTS (
    SELECT 1 FROM assets WHERE active_processed_result_id = OLD.id
 )
BEGIN
    SELECT RAISE(ABORT, 'processed_result_active_cannot_be_superseded');
END;

CREATE TRIGGER prevent_superseded_processed_result_update
BEFORE UPDATE ON processed_results
WHEN OLD.status = 'superseded'
BEGIN
    SELECT RAISE(ABORT, 'processed_result_superseded_is_immutable');
END;

CREATE TRIGGER prevent_failed_processed_result_ready_transition
BEFORE UPDATE OF status ON processed_results
WHEN OLD.status = 'failed'
 AND NEW.status <> 'failed'
BEGIN
    SELECT RAISE(ABORT, 'processed_result_failed_cannot_be_ready');
END;

CREATE TRIGGER prevent_processed_result_delete
BEFORE DELETE ON processed_results
WHEN OLD.status IN ('ready', 'superseded')
 OR EXISTS (SELECT 1 FROM assets WHERE active_processed_result_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'processed_result_delete_not_allowed');
END;

CREATE TRIGGER prevent_processed_result_derived_file_update
BEFORE UPDATE OF asset_id, kind, mime_type ON derived_files
WHEN EXISTS (
    SELECT 1
    FROM processed_results
    WHERE derived_file_id = OLD.id
      AND status IN ('ready', 'superseded')
)
BEGIN
    SELECT RAISE(ABORT, 'processed_result_derived_file_is_immutable');
END;

CREATE TRIGGER prevent_processed_result_derived_file_delete
BEFORE DELETE ON derived_files
WHEN EXISTS (
    SELECT 1
    FROM processed_results
    WHERE derived_file_id = OLD.id
      AND status IN ('ready', 'superseded')
)
BEGIN
    SELECT RAISE(ABORT, 'processed_result_derived_file_delete_not_allowed');
END;

CREATE TRIGGER prevent_new_asset_active_processed_result
BEFORE INSERT ON assets
WHEN NEW.active_processed_result_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'new_asset_active_processed_result_must_be_null');
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
      AND derived_files.kind = 'preview'
      AND derived_files.mime_type LIKE 'video/%'
 )
BEGIN
    SELECT RAISE(ABORT, 'active_processed_result_invalid');
END;

CREATE TRIGGER supersede_replaced_active_processed_result
AFTER UPDATE OF active_processed_result_id ON assets
WHEN OLD.active_processed_result_id IS NOT NULL
 AND (
    NEW.active_processed_result_id IS NULL
    OR NEW.active_processed_result_id <> OLD.active_processed_result_id
 )
BEGIN
    UPDATE processed_results
    SET status = 'superseded',
        superseded_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = OLD.active_processed_result_id
      AND status = 'ready';
END;
