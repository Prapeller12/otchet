-- Preserve exchange metadata and individual value provenance with the staged batch.
ALTER TABLE import_batches ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(metadata_json));
ALTER TABLE import_batches ADD COLUMN skipped_count INTEGER NOT NULL DEFAULT 0
    CHECK (skipped_count >= 0);
ALTER TABLE import_rows ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(provenance_json));
