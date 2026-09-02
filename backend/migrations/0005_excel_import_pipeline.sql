-- Auditable two-phase Excel import pipeline.
--
-- Source books are copied to imports/inbox. Parsed values remain in staging
-- until the user confirms the preview; committed facts stay in the existing
-- immutable report_fact_revisions ledger.

CREATE TABLE import_batches (
    id TEXT PRIMARY KEY CHECK (length(id) = 32),
    report_type TEXT NOT NULL
        CHECK (report_type IN ('DAILY_MOVEMENT', 'HEAD_SITE', 'SUBSIDIARY')),
    organization_id INTEGER NOT NULL,
    source_file_name TEXT NOT NULL CHECK (length(trim(source_file_name)) > 0),
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    stored_relative_path TEXT NOT NULL CHECK (length(trim(stored_relative_path)) > 0),
    status TEXT NOT NULL CHECK (status IN ('STAGED', 'INVALID', 'COMMITTED')),
    new_count INTEGER NOT NULL DEFAULT 0 CHECK (new_count >= 0),
    changed_count INTEGER NOT NULL DEFAULT 0 CHECK (changed_count >= 0),
    same_count INTEGER NOT NULL DEFAULT 0 CHECK (same_count >= 0),
    error_count INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    committed_at TEXT,
    FOREIGN KEY (organization_id) REFERENCES organizations (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE TABLE import_rows (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL,
    source_cell TEXT NOT NULL CHECK (length(trim(source_cell)) > 0),
    coordinate_json TEXT NOT NULL CHECK (json_valid(coordinate_json)),
    classification TEXT NOT NULL CHECK (classification IN ('NEW', 'CHANGED', 'SAME')),
    value_kind TEXT NOT NULL CHECK (value_kind IN ('DATA_NOT_PROVIDED', 'QUANTITY')),
    quantity TEXT,
    expected_revision INTEGER CHECK (expected_revision IS NULL OR expected_revision > 0),
    CHECK (
        (value_kind = 'DATA_NOT_PROVIDED' AND quantity IS NULL)
        OR
        (value_kind = 'QUANTITY' AND quantity IS NOT NULL)
    ),
    UNIQUE (batch_id, source_cell),
    FOREIGN KEY (batch_id) REFERENCES import_batches (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE TABLE import_errors (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL,
    source_cell TEXT,
    code TEXT NOT NULL CHECK (length(trim(code)) > 0),
    message TEXT NOT NULL CHECK (length(trim(message)) > 0),
    FOREIGN KEY (batch_id) REFERENCES import_batches (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE INDEX idx_import_batches_scope
    ON import_batches (report_type, organization_id, created_at, id);

CREATE UNIQUE INDEX idx_import_batches_committed_source
    ON import_batches (report_type, organization_id, source_sha256)
    WHERE status = 'COMMITTED';

CREATE INDEX idx_import_rows_batch ON import_rows (batch_id, id);
CREATE INDEX idx_import_errors_batch ON import_errors (batch_id, id);

CREATE TRIGGER import_batches_no_delete
BEFORE DELETE ON import_batches
BEGIN
    SELECT RAISE(ABORT, 'import batches are immutable');
END;

CREATE TRIGGER import_rows_no_update
BEFORE UPDATE ON import_rows
BEGIN
    SELECT RAISE(ABORT, 'import rows are immutable');
END;

CREATE TRIGGER import_rows_no_delete
BEFORE DELETE ON import_rows
BEGIN
    SELECT RAISE(ABORT, 'import rows are immutable');
END;

CREATE TRIGGER import_errors_no_update
BEFORE UPDATE ON import_errors
BEGIN
    SELECT RAISE(ABORT, 'import errors are immutable');
END;

CREATE TRIGGER import_errors_no_delete
BEFORE DELETE ON import_errors
BEGIN
    SELECT RAISE(ABORT, 'import errors are immutable');
END;
