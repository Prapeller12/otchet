-- Transport-neutral report fact declarations for the first portable vertical slice.
--
-- The mapping between a canonical report form and these business coordinates is
-- still a WORKING_REFERENCE. No Excel address, role, plan rule or organization
-- catalogue is inferred by this migration.

CREATE TABLE report_fact_revisions (
    id INTEGER PRIMARY KEY,
    report_type TEXT NOT NULL
        CHECK (report_type IN ('DAILY_MOVEMENT', 'HEAD_SITE', 'SUBSIDIARY')),
    organization_id INTEGER NOT NULL,
    product_id INTEGER,
    component_id INTEGER,
    metric_code TEXT,
    operation_type TEXT,
    operation_date TEXT,
    period_start TEXT,
    bom_version_id INTEGER,
    value_kind TEXT NOT NULL
        CHECK (value_kind IN ('DATA_NOT_PROVIDED', 'QUANTITY')),
    quantity TEXT CHECK (
        quantity IS NULL
        OR (
            length(quantity) > 0
            AND (
                quantity NOT GLOB '*[^0-9.]*'
                OR (
                    substr(quantity, 1, 1) = '-'
                    AND substr(quantity, 2) NOT GLOB '*[^0-9.]*'
                )
            )
            AND length(ltrim(quantity, '-')) > 0
            AND length(ltrim(quantity, '-'))
                - length(replace(ltrim(quantity, '-'), '.', '')) <= 1
            AND substr(ltrim(quantity, '-'), 1, 1) <> '.'
            AND substr(ltrim(quantity, '-'), -1, 1) <> '.'
            AND (
                length(ltrim(quantity, '-')) = 1
                OR substr(ltrim(quantity, '-'), 1, 1) <> '0'
                OR substr(ltrim(quantity, '-'), 2, 1) = '.'
            )
        )
    ),
    revision INTEGER NOT NULL CHECK (revision > 0),
    previous_revision_id INTEGER UNIQUE,
    contract_status TEXT NOT NULL DEFAULT 'WORKING_REFERENCE'
        CHECK (contract_status = 'WORKING_REFERENCE'),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    coordinate_key TEXT GENERATED ALWAYS AS (
        report_type || '|ORG:' || organization_id
        || CASE
            WHEN product_id IS NOT NULL THEN '|PRODUCT:' || product_id
            ELSE '|COMPONENT:' || component_id
        END
        || CASE
            WHEN metric_code IS NOT NULL THEN '|METRIC:' || metric_code
            ELSE '|OPERATION:' || operation_type
        END
        || CASE
            WHEN operation_date IS NOT NULL THEN '|DATE:' || operation_date
            ELSE '|PERIOD:' || period_start
        END
        || CASE
            WHEN bom_version_id IS NOT NULL THEN '|BOM:' || bom_version_id
            ELSE ''
        END
    ) STORED,
    CHECK ((product_id IS NOT NULL) <> (component_id IS NOT NULL)),
    CHECK ((metric_code IS NOT NULL) <> (operation_type IS NOT NULL)),
    CHECK ((operation_date IS NOT NULL) <> (period_start IS NOT NULL)),
    CHECK (metric_code IS NULL OR metric_code GLOB '[A-Z][A-Z0-9_]*'),
    CHECK (operation_type IS NULL OR operation_type GLOB '[A-Z][A-Z0-9_]*'),
    CHECK (
        operation_date IS NULL
        OR (
            length(operation_date) = 10
            AND operation_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
            AND date(operation_date, '+0 days') = operation_date
        )
    ),
    CHECK (
        period_start IS NULL
        OR (
            length(period_start) = 10
            AND period_start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
            AND date(period_start, '+0 days') = period_start
        )
    ),
    CHECK (
        (value_kind = 'DATA_NOT_PROVIDED' AND quantity IS NULL)
        OR (value_kind = 'QUANTITY' AND quantity IS NOT NULL)
    ),
    UNIQUE (coordinate_key, revision),
    FOREIGN KEY (organization_id) REFERENCES organizations (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, product_id)
        REFERENCES products (organization_id, id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, component_id)
        REFERENCES components (organization_id, id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, bom_version_id)
        REFERENCES bom_versions (organization_id, id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (previous_revision_id) REFERENCES report_fact_revisions (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE INDEX idx_report_fact_current_scope
    ON report_fact_revisions (report_type, organization_id, operation_date, period_start);

CREATE TRIGGER report_fact_revision_chain_insert
BEFORE INSERT ON report_fact_revisions
BEGIN
    SELECT RAISE(ABORT, 'first report fact revision must be revision 1')
    WHERE NEW.previous_revision_id IS NULL AND NEW.revision <> 1;

    SELECT RAISE(ABORT, 'report fact revision must extend the current coordinate revision')
    WHERE NEW.previous_revision_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1
        FROM report_fact_revisions AS previous
        WHERE previous.id = NEW.previous_revision_id
          AND previous.coordinate_key = NEW.coordinate_key
          AND previous.revision + 1 = NEW.revision
          AND NOT EXISTS (
            SELECT 1
            FROM report_fact_revisions AS successor
            WHERE successor.previous_revision_id = previous.id
          )
      );
END;

CREATE TRIGGER report_fact_revisions_immutable
BEFORE UPDATE ON report_fact_revisions
BEGIN
    SELECT RAISE(ABORT, 'report fact revisions are immutable');
END;

CREATE TRIGGER report_fact_revisions_no_delete
BEFORE DELETE ON report_fact_revisions
BEGIN
    SELECT RAISE(ABORT, 'report fact revisions cannot be deleted');
END;

CREATE TABLE idempotency_records (
    id INTEGER PRIMARY KEY,
    command_name TEXT NOT NULL CHECK (length(trim(command_name)) > 0),
    idempotency_key TEXT NOT NULL CHECK (length(trim(idempotency_key)) > 0),
    request_sha256 TEXT NOT NULL CHECK (
        length(request_sha256) = 64
        AND request_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    response_json TEXT NOT NULL CHECK (json_valid(response_json)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (command_name, idempotency_key)
) STRICT;

CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY,
    actor_ref TEXT NOT NULL CHECK (length(trim(actor_ref)) > 0),
    entity_type TEXT NOT NULL CHECK (length(trim(entity_type)) > 0),
    entity_id TEXT NOT NULL CHECK (length(trim(entity_id)) > 0),
    action TEXT NOT NULL CHECK (length(trim(action)) > 0),
    before_json TEXT CHECK (before_json IS NULL OR json_valid(before_json)),
    after_json TEXT NOT NULL CHECK (json_valid(after_json)),
    contract_status TEXT NOT NULL DEFAULT 'WORKING_REFERENCE'
        CHECK (contract_status = 'WORKING_REFERENCE'),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX idx_audit_entity
    ON audit_events (entity_type, entity_id, created_at);
