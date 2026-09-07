CREATE TABLE report_verifications (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    report_type TEXT NOT NULL CHECK (report_type IN ('DAILY_MOVEMENT','HEAD_SITE','SUBSIDIARY')),
    period TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL CHECK (length(snapshot_sha256) = 64),
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    signer_name TEXT NOT NULL CHECK (length(trim(signer_name)) BETWEEN 1 AND 120),
    signed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE INDEX report_verifications_lookup ON report_verifications(organization_id, report_type, period, id);
CREATE TRIGGER report_verifications_no_update BEFORE UPDATE ON report_verifications
BEGIN SELECT RAISE(ABORT, 'Verification records are immutable'); END;
CREATE TRIGGER report_verifications_no_delete BEFORE DELETE ON report_verifications
BEGIN SELECT RAISE(ABORT, 'Verification records are immutable'); END;
