-- Canonical workbook evidence travels only inside the encrypted database snapshot.
-- SHA-256 deduplicates bytes; batch bindings preserve each reviewed import context.
CREATE TABLE import_workbook_sources (
    source_sha256 TEXT PRIMARY KEY CHECK (
        length(source_sha256) = 64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    workbook_bytes BLOB NOT NULL,
    byte_count INTEGER NOT NULL CHECK (
        byte_count > 0 AND byte_count <= 52428800 AND byte_count = length(workbook_bytes)
    ),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE import_batch_sources (
    batch_id TEXT PRIMARY KEY,
    source_sha256 TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES import_batches (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (source_sha256) REFERENCES import_workbook_sources (source_sha256)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE TRIGGER import_workbook_sources_no_update
BEFORE UPDATE ON import_workbook_sources
BEGIN
    SELECT RAISE(ABORT, 'import workbook evidence is immutable');
END;

CREATE TRIGGER import_workbook_sources_no_delete
BEFORE DELETE ON import_workbook_sources
BEGIN
    SELECT RAISE(ABORT, 'import workbook evidence is immutable');
END;

CREATE TRIGGER import_batch_sources_no_update
BEFORE UPDATE ON import_batch_sources
BEGIN
    SELECT RAISE(ABORT, 'import source binding is immutable');
END;

CREATE TRIGGER import_batch_sources_no_delete
BEFORE DELETE ON import_batch_sources
BEGIN
    SELECT RAISE(ABORT, 'import source binding is immutable');
END;
