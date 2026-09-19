ALTER TABLE report_verifications ADD COLUMN signature_version INTEGER NOT NULL DEFAULT 0
    CHECK (signature_version IN (0, 1));

CREATE TABLE report_signers (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL UNIQUE CHECK (length(trim(display_name)) BETWEEN 1 AND 120),
    role TEXT NOT NULL CHECK (role IN ('admin', 'signer')),
    public_key TEXT NOT NULL UNIQUE,
    key_fingerprint TEXT NOT NULL,
    encrypted_private_key TEXT NOT NULL,
    salt TEXT NOT NULL,
    nonce TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;
CREATE TRIGGER report_signers_no_update BEFORE UPDATE ON report_signers
BEGIN SELECT RAISE(ABORT, 'Signing profiles are immutable'); END;
CREATE TRIGGER report_signers_no_delete BEFORE DELETE ON report_signers
BEGIN SELECT RAISE(ABORT, 'Signing profiles are immutable'); END;

CREATE TABLE report_signatures (
    verification_id INTEGER PRIMARY KEY REFERENCES report_verifications(id),
    signer_id TEXT NOT NULL REFERENCES report_signers(id),
    event_id TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    public_key TEXT NOT NULL,
    signature TEXT NOT NULL
) STRICT;
CREATE TRIGGER report_signatures_no_update BEFORE UPDATE ON report_signatures
BEGIN SELECT RAISE(ABORT, 'Signatures are immutable'); END;
CREATE TRIGGER report_signatures_no_delete BEFORE DELETE ON report_signatures
BEGIN SELECT RAISE(ABORT, 'Signatures are immutable'); END;
