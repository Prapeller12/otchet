-- Preserve every existing immutable audit event while extending accepted roles.
CREATE TABLE report_access_events_expanded (
    id INTEGER PRIMARY KEY,
    signer_id TEXT NOT NULL REFERENCES report_signers(id),
    role TEXT NOT NULL CHECK (role IN ('admin', 'reviewer', 'project_manager')),
    command TEXT NOT NULL CHECK (length(command) BETWEEN 1 AND 80),
    outcome TEXT NOT NULL CHECK (outcome IN ('authorized', 'succeeded', 'failed')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
INSERT INTO report_access_events_expanded SELECT * FROM report_access_events;
DROP TABLE report_access_events;
ALTER TABLE report_access_events_expanded RENAME TO report_access_events;
CREATE TRIGGER report_access_events_no_update BEFORE UPDATE ON report_access_events
BEGIN SELECT RAISE(ABORT, 'Access audit events are immutable'); END;
CREATE TRIGGER report_access_events_no_delete BEFORE DELETE ON report_access_events
BEGIN SELECT RAISE(ABORT, 'Access audit events are immutable'); END;
