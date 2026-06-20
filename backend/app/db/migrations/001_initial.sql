CREATE TABLE IF NOT EXISTS assets (
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS derived_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS jobs (
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
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_claim
ON jobs (status, created_at);

CREATE INDEX IF NOT EXISTS idx_jobs_lease_recovery
ON jobs (status, lease_expires_at);
