-- User-configurable WORKING_REFERENCE workspaces.
--
-- Layout declarations are intentionally separate from immutable report facts.
-- Rows and organizations are archived instead of being physically deleted so
-- existing facts remain addressable and auditable.

CREATE TABLE report_workspace_profiles (
    organization_id INTEGER PRIMARY KEY,
    workspace_kind TEXT NOT NULL
        CHECK (workspace_kind IN ('HEAD', 'SUBSIDIARY')),
    sort_order INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (organization_id) REFERENCES organizations (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE TABLE report_workspace_groups (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    report_type TEXT NOT NULL
        CHECK (report_type IN ('DAILY_MOVEMENT', 'HEAD_SITE', 'SUBSIDIARY')),
    template_group_id TEXT NOT NULL CHECK (length(trim(template_group_id)) > 0),
    party_name TEXT NOT NULL CHECK (length(trim(party_name)) > 0),
    position_name TEXT NOT NULL CHECK (length(trim(position_name)) > 0),
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('product', 'component')),
    product_id INTEGER,
    component_id INTEGER,
    sort_order INTEGER NOT NULL CHECK (sort_order >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (
        (subject_kind = 'product' AND product_id IS NOT NULL AND component_id IS NULL)
        OR
        (subject_kind = 'component' AND component_id IS NOT NULL AND product_id IS NULL)
    ),
    UNIQUE (organization_id, report_type, id),
    FOREIGN KEY (organization_id) REFERENCES organizations (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, product_id)
        REFERENCES products (organization_id, id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, component_id)
        REFERENCES components (organization_id, id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE INDEX idx_report_workspace_groups_scope
    ON report_workspace_groups (organization_id, report_type, is_active, sort_order, id);

CREATE TRIGGER report_workspace_groups_no_delete
BEFORE DELETE ON report_workspace_groups
BEGIN
    SELECT RAISE(ABORT, 'report workspace groups must be archived');
END;
