-- Preserve historical signing identities/signatures while allowing PIN rewrapping
-- and controlled reassignment of application access roles.
DROP TRIGGER report_signers_no_update;
CREATE TRIGGER report_signers_identity_immutable BEFORE UPDATE ON report_signers
WHEN NEW.id != OLD.id OR NEW.display_name != OLD.display_name OR NEW.role != OLD.role
  OR NEW.public_key != OLD.public_key OR NEW.key_fingerprint != OLD.key_fingerprint
  OR NEW.created_at != OLD.created_at
BEGIN SELECT RAISE(ABORT, 'Signing identities are immutable'); END;
DROP TRIGGER report_access_roles_identity;
DROP TRIGGER report_access_roles_no_update;
CREATE TABLE report_signer_status (
    signer_id TEXT PRIMARY KEY REFERENCES report_signers(id),
    revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1))
) STRICT;
INSERT INTO report_signer_status(signer_id) SELECT id FROM report_signers;
CREATE TRIGGER report_signer_status_created AFTER INSERT ON report_signers
BEGIN INSERT INTO report_signer_status(signer_id) VALUES (NEW.id); END;
CREATE TRIGGER report_signer_admin_no_revoke BEFORE UPDATE ON report_signer_status
WHEN NEW.revoked = 1 AND EXISTS (
    SELECT 1 FROM report_access_roles WHERE signer_id=NEW.signer_id AND role='admin'
)
BEGIN SELECT RAISE(ABORT, 'Transfer administrator before revoking access'); END;
CREATE TABLE report_access_lifecycle_commits (
    operation_id TEXT PRIMARY KEY,
    action TEXT NOT NULL CHECK (action IN ('change_pin', 'revoke', 'transfer_admin')),
    signer_id TEXT NOT NULL REFERENCES report_signers(id),
    actor_id TEXT NOT NULL REFERENCES report_signers(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE TRIGGER report_access_lifecycle_no_update BEFORE UPDATE ON report_access_lifecycle_commits
BEGIN SELECT RAISE(ABORT, 'Access lifecycle history is immutable'); END;
CREATE TRIGGER report_access_lifecycle_no_delete BEFORE DELETE ON report_access_lifecycle_commits
BEGIN SELECT RAISE(ABORT, 'Access lifecycle history is immutable'); END;
