-- An authoritative receipt travels in the same encrypted generation as its effect.
CREATE TABLE network_operation_receipts (
    operation_id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
    actor_id TEXT NOT NULL,
    global_revision TEXT NOT NULL,
    result_json TEXT NOT NULL CHECK (json_valid(result_json)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE TRIGGER network_receipts_no_update BEFORE UPDATE ON network_operation_receipts
BEGIN SELECT RAISE(ABORT, 'Network receipts are immutable'); END;
CREATE TRIGGER network_receipts_no_delete BEFORE DELETE ON network_operation_receipts
BEGIN SELECT RAISE(ABORT, 'Network receipts are immutable'); END;
