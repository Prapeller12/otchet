-- Durable exchange identity, provenance bindings and reviewed import packages.
CREATE TABLE exchange_dataset_identity (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    dataset_id TEXT NOT NULL UNIQUE CHECK(length(dataset_id)=32)
) STRICT;
INSERT INTO exchange_dataset_identity VALUES(1,lower(hex(randomblob(16))));
CREATE TABLE exchange_entity_bindings (
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    report_type TEXT NOT NULL CHECK(report_type IN ('SUBSIDIARY','HEAD_SITE','DAILY_MOVEMENT')),
    semantic_key TEXT NOT NULL,
    group_id INTEGER NOT NULL REFERENCES report_workspace_groups(id),
    identity_json TEXT NOT NULL CHECK(json_valid(identity_json)),
    PRIMARY KEY(organization_id,report_type,semantic_key)
) STRICT;
CREATE TABLE exchange_import_packages (
    id TEXT PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    report_type TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('STAGED','COMMITTED')),
    package_json TEXT NOT NULL CHECK(json_valid(package_json)),
    created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    committed_at TEXT
) STRICT;
