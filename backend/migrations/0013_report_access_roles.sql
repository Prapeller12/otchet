-- Access roles are separate from immutable signing identities: the original
-- signing role is authenticated key metadata and cannot be rewritten on upgrade.
CREATE UNIQUE INDEX report_signers_single_admin ON report_signers(role) WHERE role = 'admin';

CREATE TABLE report_access_roles (
    signer_id TEXT PRIMARY KEY REFERENCES report_signers(id),
    role TEXT NOT NULL CHECK (role IN ('admin', 'reviewer', 'project_manager'))
) STRICT;
CREATE UNIQUE INDEX report_access_roles_single_admin ON report_access_roles(role)
    WHERE role = 'admin';
INSERT INTO report_access_roles(signer_id, role)
    SELECT id, CASE role WHEN 'admin' THEN 'admin' ELSE 'reviewer' END FROM report_signers;
CREATE TRIGGER report_access_roles_identity BEFORE INSERT ON report_access_roles
WHEN (NEW.role = 'admin') != ((SELECT role FROM report_signers WHERE id = NEW.signer_id) = 'admin')
BEGIN SELECT RAISE(ABORT, 'Administrator must match original signing identity'); END;
CREATE TRIGGER report_access_roles_no_update BEFORE UPDATE ON report_access_roles
BEGIN SELECT RAISE(ABORT, 'Access roles are immutable'); END;
CREATE TRIGGER report_access_roles_no_delete BEFORE DELETE ON report_access_roles
BEGIN SELECT RAISE(ABORT, 'Access roles are immutable'); END;

CREATE TABLE report_access_events (
    id INTEGER PRIMARY KEY,
    signer_id TEXT NOT NULL REFERENCES report_signers(id),
    role TEXT NOT NULL CHECK (role IN ('admin', 'reviewer')),
    command TEXT NOT NULL CHECK (length(command) BETWEEN 1 AND 80),
    outcome TEXT NOT NULL CHECK (outcome IN ('authorized', 'succeeded', 'failed')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE TRIGGER report_access_events_no_update BEFORE UPDATE ON report_access_events
BEGIN SELECT RAISE(ABORT, 'Access audit events are immutable'); END;
CREATE TRIGGER report_access_events_no_delete BEFORE DELETE ON report_access_events
BEGIN SELECT RAISE(ABORT, 'Access audit events are immutable'); END;
