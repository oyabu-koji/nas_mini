ALTER TABLE jobs ADD COLUMN dedup_key TEXT;

UPDATE jobs
SET dedup_key = 'legacy:' || id
WHERE dedup_key IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dedup_key
ON jobs (dedup_key)
WHERE dedup_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_original_path
ON assets (original_path)
WHERE original_path IS NOT NULL;

CREATE TABLE IF NOT EXISTS upload_sessions (
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

CREATE INDEX IF NOT EXISTS idx_upload_sessions_active
ON upload_sessions (status, expires_at);

CREATE TABLE IF NOT EXISTS upload_chunks (
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

CREATE INDEX IF NOT EXISTS idx_upload_chunks_session
ON upload_chunks (session_id, chunk_index);
