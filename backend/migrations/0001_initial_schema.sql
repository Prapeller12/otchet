-- Initial SQLite data foundation.
--
-- Quantities are stored as validated plain-decimal TEXT.  SQLite REAL is not
-- used because it cannot represent decimal quantities exactly.  Application
-- code must parse these values with decimal.Decimal; SQL casts to REAL/NUMERIC
-- are not an authoritative calculation path.

CREATE TABLE organizations (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE CHECK (length(trim(code)) > 0),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    parent_id INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (parent_id IS NULL OR parent_id <> id),
    FOREIGN KEY (parent_id) REFERENCES organizations (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    code TEXT NOT NULL CHECK (length(trim(code)) > 0),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    modification TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id) REFERENCES organizations (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE UNIQUE INDEX uq_products_scope_code_modification
    ON products (organization_id, code, coalesce(modification, ''));

CREATE TABLE components (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    code TEXT NOT NULL CHECK (length(trim(code)) > 0),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    kind TEXT NOT NULL CHECK (length(trim(kind)) > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (organization_id, id),
    UNIQUE (organization_id, code, kind),
    FOREIGN KEY (organization_id) REFERENCES organizations (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE TABLE bom_versions (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    version TEXT NOT NULL CHECK (length(trim(version)) > 0),
    valid_from TEXT NOT NULL CHECK (
        length(valid_from) = 10
        AND valid_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
        AND date(valid_from, '+0 days') = valid_from
    ),
    valid_to TEXT CHECK (
        valid_to IS NULL
        OR (
            length(valid_to) = 10
            AND valid_to GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
            AND date(valid_to, '+0 days') = valid_to
        )
    ),
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'APPROVED', 'RETIRED')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (valid_to IS NULL OR valid_to >= valid_from),
    UNIQUE (organization_id, id),
    UNIQUE (organization_id, product_id, version),
    FOREIGN KEY (organization_id, product_id)
        REFERENCES products (organization_id, id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE INDEX idx_bom_active
    ON bom_versions (organization_id, product_id, status, valid_from, valid_to);

CREATE TRIGGER bom_versions_approved_no_overlap_insert
BEFORE INSERT ON bom_versions
WHEN NEW.status = 'APPROVED'
BEGIN
    SELECT RAISE(ABORT, 'approved BOM validity periods must not overlap')
    WHERE EXISTS (
        SELECT 1
        FROM bom_versions AS existing
        WHERE existing.organization_id = NEW.organization_id
          AND existing.product_id = NEW.product_id
          AND existing.status = 'APPROVED'
          AND NEW.valid_from <= coalesce(existing.valid_to, '9999-12-31')
          AND existing.valid_from <= coalesce(NEW.valid_to, '9999-12-31')
    );
END;

CREATE TRIGGER bom_versions_approved_no_overlap_update
BEFORE UPDATE OF organization_id, product_id, valid_from, valid_to, status ON bom_versions
WHEN NEW.status = 'APPROVED'
BEGIN
    SELECT RAISE(ABORT, 'approved BOM validity periods must not overlap')
    WHERE EXISTS (
        SELECT 1
        FROM bom_versions AS existing
        WHERE existing.id <> OLD.id
          AND existing.organization_id = NEW.organization_id
          AND existing.product_id = NEW.product_id
          AND existing.status = 'APPROVED'
          AND NEW.valid_from <= coalesce(existing.valid_to, '9999-12-31')
          AND existing.valid_from <= coalesce(NEW.valid_to, '9999-12-31')
    );
END;

CREATE TABLE bom_items (
    organization_id INTEGER NOT NULL,
    bom_version_id INTEGER NOT NULL,
    component_id INTEGER NOT NULL,
    qty_per_product TEXT NOT NULL CHECK (
        length(qty_per_product) > 0
        AND qty_per_product NOT GLOB '*[^0-9.]*'
        AND length(qty_per_product) - length(replace(qty_per_product, '.', '')) <= 1
        AND substr(qty_per_product, 1, 1) <> '.'
        AND substr(qty_per_product, -1, 1) <> '.'
        AND (
            length(qty_per_product) = 1
            OR substr(qty_per_product, 1, 1) <> '0'
            OR substr(qty_per_product, 2, 1) = '.'
        )
        AND replace(replace(qty_per_product, '0', ''), '.', '') <> ''
    ),
    loss_factor TEXT NOT NULL DEFAULT '0' CHECK (
        length(loss_factor) > 0
        AND loss_factor NOT GLOB '*[^0-9.]*'
        AND length(loss_factor) - length(replace(loss_factor, '.', '')) <= 1
        AND substr(loss_factor, 1, 1) <> '.'
        AND substr(loss_factor, -1, 1) <> '.'
        AND (
            length(loss_factor) = 1
            OR substr(loss_factor, 1, 1) <> '0'
            OR substr(loss_factor, 2, 1) = '.'
        )
    ),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (organization_id, bom_version_id, component_id),
    FOREIGN KEY (organization_id, bom_version_id)
        REFERENCES bom_versions (organization_id, id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, component_id)
        REFERENCES components (organization_id, id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE TABLE stock_operations (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    component_id INTEGER NOT NULL,
    operation_type TEXT NOT NULL CHECK (length(trim(operation_type)) > 0),
    quantity TEXT NOT NULL CHECK (
        length(quantity) > 0
        AND (
            quantity NOT GLOB '*[^0-9.]*'
            OR (
                operation_type = 'SIGNED_ADJUSTMENT'
                AND substr(quantity, 1, 1) = '-'
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
        AND replace(replace(ltrim(quantity, '-'), '0', ''), '.', '') <> ''
    ),
    operation_at TEXT NOT NULL CHECK (
        length(operation_at) IN (20, 25)
        AND substr(operation_at, 5, 1) = '-'
        AND substr(operation_at, 8, 1) = '-'
        AND substr(operation_at, 11, 1) = 'T'
        AND substr(operation_at, 14, 1) = ':'
        AND substr(operation_at, 17, 1) = ':'
        AND substr(operation_at, 1, 4) NOT GLOB '*[^0-9]*'
        AND substr(operation_at, 6, 2) NOT GLOB '*[^0-9]*'
        AND substr(operation_at, 9, 2) NOT GLOB '*[^0-9]*'
        AND substr(operation_at, 12, 2) NOT GLOB '*[^0-9]*'
        AND substr(operation_at, 15, 2) NOT GLOB '*[^0-9]*'
        AND substr(operation_at, 18, 2) NOT GLOB '*[^0-9]*'
        AND date(substr(operation_at, 1, 10), '+0 days') = substr(operation_at, 1, 10)
        AND time(substr(operation_at, 12, 8), '+0 seconds') = substr(operation_at, 12, 8)
        AND (
            (length(operation_at) = 20 AND substr(operation_at, 20, 1) = 'Z')
            OR (
                length(operation_at) = 25
                AND substr(operation_at, 20, 1) IN ('+', '-')
                AND substr(operation_at, 23, 1) = ':'
                AND substr(operation_at, 21, 2) NOT GLOB '*[^0-9]*'
                AND substr(operation_at, 24, 2) NOT GLOB '*[^0-9]*'
                AND CAST(substr(operation_at, 21, 2) AS INTEGER) <= 14
                AND CAST(substr(operation_at, 24, 2) AS INTEGER) <= 59
                AND (
                    CAST(substr(operation_at, 21, 2) AS INTEGER) < 14
                    OR CAST(substr(operation_at, 24, 2) AS INTEGER) = 0
                )
            )
        )
        AND datetime(operation_at) IS NOT NULL
    ),
    document_ref TEXT,
    comment TEXT,
    status TEXT NOT NULL DEFAULT 'POSTED' CHECK (status IN ('POSTED', 'REVERSED')),
    reverses_operation_id INTEGER,
    reversal_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (reverses_operation_id IS NULL OR reverses_operation_id <> id),
    CHECK (
        (reverses_operation_id IS NULL AND reversal_reason IS NULL)
        OR (
            reverses_operation_id IS NOT NULL
            AND reversal_reason IS NOT NULL
            AND length(trim(reversal_reason)) > 0
        )
    ),
    UNIQUE (organization_id, component_id, id),
    UNIQUE (reverses_operation_id),
    FOREIGN KEY (organization_id, component_id)
        REFERENCES components (organization_id, id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, component_id, reverses_operation_id)
        REFERENCES stock_operations (organization_id, component_id, id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE INDEX idx_stock_operations_org_component_date
    ON stock_operations (organization_id, component_id, operation_at);

CREATE TRIGGER stock_operations_insert_must_be_posted
BEFORE INSERT ON stock_operations
WHEN NEW.status <> 'POSTED'
BEGIN
    SELECT RAISE(ABORT, 'new stock operations must be posted');
END;

CREATE TRIGGER stock_operations_validate_reversal
BEFORE INSERT ON stock_operations
WHEN NEW.reverses_operation_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'stock reversal must exactly match an unreversed posted operation')
    WHERE NOT EXISTS (
        SELECT 1
        FROM stock_operations AS original
        WHERE original.id = NEW.reverses_operation_id
          AND original.organization_id = NEW.organization_id
          AND original.component_id = NEW.component_id
          AND original.status = 'POSTED'
          AND original.reverses_operation_id IS NULL
          AND original.operation_type = NEW.operation_type
          AND original.quantity = NEW.quantity
    );
END;

CREATE TRIGGER stock_operations_mark_reversed
AFTER INSERT ON stock_operations
WHEN NEW.reverses_operation_id IS NOT NULL
BEGIN
    UPDATE stock_operations
    SET status = 'REVERSED'
    WHERE id = NEW.reverses_operation_id;
END;

CREATE TRIGGER stock_operations_immutable
BEFORE UPDATE ON stock_operations
WHEN NOT (
    OLD.status = 'POSTED'
    AND NEW.status = 'REVERSED'
    AND NEW.id IS OLD.id
    AND NEW.organization_id IS OLD.organization_id
    AND NEW.component_id IS OLD.component_id
    AND NEW.operation_type IS OLD.operation_type
    AND NEW.quantity IS OLD.quantity
    AND NEW.operation_at IS OLD.operation_at
    AND NEW.document_ref IS OLD.document_ref
    AND NEW.comment IS OLD.comment
    AND NEW.reverses_operation_id IS OLD.reverses_operation_id
    AND NEW.reversal_reason IS OLD.reversal_reason
    AND NEW.created_at IS OLD.created_at
    AND EXISTS (
        SELECT 1
        FROM stock_operations AS reversal
        WHERE reversal.reverses_operation_id = OLD.id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'posted stock operations are immutable');
END;

CREATE TRIGGER stock_operations_no_delete
BEFORE DELETE ON stock_operations
WHEN OLD.status IN ('POSTED', 'REVERSED')
BEGIN
    SELECT RAISE(ABORT, 'posted stock operations cannot be deleted');
END;

CREATE TABLE product_operations (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    operation_type TEXT NOT NULL CHECK (length(trim(operation_type)) > 0),
    quantity TEXT NOT NULL CHECK (
        length(quantity) > 0
        AND quantity NOT GLOB '*[^0-9.]*'
        AND length(quantity) - length(replace(quantity, '.', '')) <= 1
        AND substr(quantity, 1, 1) <> '.'
        AND substr(quantity, -1, 1) <> '.'
        AND (
            length(quantity) = 1
            OR substr(quantity, 1, 1) <> '0'
            OR substr(quantity, 2, 1) = '.'
        )
        AND replace(replace(quantity, '0', ''), '.', '') <> ''
    ),
    operation_at TEXT NOT NULL CHECK (
        length(operation_at) IN (20, 25)
        AND substr(operation_at, 5, 1) = '-'
        AND substr(operation_at, 8, 1) = '-'
        AND substr(operation_at, 11, 1) = 'T'
        AND substr(operation_at, 14, 1) = ':'
        AND substr(operation_at, 17, 1) = ':'
        AND substr(operation_at, 1, 4) NOT GLOB '*[^0-9]*'
        AND substr(operation_at, 6, 2) NOT GLOB '*[^0-9]*'
        AND substr(operation_at, 9, 2) NOT GLOB '*[^0-9]*'
        AND substr(operation_at, 12, 2) NOT GLOB '*[^0-9]*'
        AND substr(operation_at, 15, 2) NOT GLOB '*[^0-9]*'
        AND substr(operation_at, 18, 2) NOT GLOB '*[^0-9]*'
        AND date(substr(operation_at, 1, 10), '+0 days') = substr(operation_at, 1, 10)
        AND time(substr(operation_at, 12, 8), '+0 seconds') = substr(operation_at, 12, 8)
        AND (
            (length(operation_at) = 20 AND substr(operation_at, 20, 1) = 'Z')
            OR (
                length(operation_at) = 25
                AND substr(operation_at, 20, 1) IN ('+', '-')
                AND substr(operation_at, 23, 1) = ':'
                AND substr(operation_at, 21, 2) NOT GLOB '*[^0-9]*'
                AND substr(operation_at, 24, 2) NOT GLOB '*[^0-9]*'
                AND CAST(substr(operation_at, 21, 2) AS INTEGER) <= 14
                AND CAST(substr(operation_at, 24, 2) AS INTEGER) <= 59
                AND (
                    CAST(substr(operation_at, 21, 2) AS INTEGER) < 14
                    OR CAST(substr(operation_at, 24, 2) AS INTEGER) = 0
                )
            )
        )
        AND datetime(operation_at) IS NOT NULL
    ),
    document_ref TEXT,
    comment TEXT,
    status TEXT NOT NULL DEFAULT 'POSTED' CHECK (status IN ('POSTED', 'REVERSED')),
    reverses_operation_id INTEGER,
    reversal_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (reverses_operation_id IS NULL OR reverses_operation_id <> id),
    CHECK (
        (reverses_operation_id IS NULL AND reversal_reason IS NULL)
        OR (
            reverses_operation_id IS NOT NULL
            AND reversal_reason IS NOT NULL
            AND length(trim(reversal_reason)) > 0
        )
    ),
    UNIQUE (organization_id, product_id, id),
    UNIQUE (reverses_operation_id),
    FOREIGN KEY (organization_id, product_id)
        REFERENCES products (organization_id, id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, product_id, reverses_operation_id)
        REFERENCES product_operations (organization_id, product_id, id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) STRICT;

CREATE INDEX idx_product_operations_org_product_date
    ON product_operations (organization_id, product_id, operation_at);

CREATE TRIGGER product_operations_insert_must_be_posted
BEFORE INSERT ON product_operations
WHEN NEW.status <> 'POSTED'
BEGIN
    SELECT RAISE(ABORT, 'new product operations must be posted');
END;

CREATE TRIGGER product_operations_validate_reversal
BEFORE INSERT ON product_operations
WHEN NEW.reverses_operation_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'product reversal must exactly match an unreversed posted operation')
    WHERE NOT EXISTS (
        SELECT 1
        FROM product_operations AS original
        WHERE original.id = NEW.reverses_operation_id
          AND original.organization_id = NEW.organization_id
          AND original.product_id = NEW.product_id
          AND original.status = 'POSTED'
          AND original.reverses_operation_id IS NULL
          AND original.operation_type = NEW.operation_type
          AND original.quantity = NEW.quantity
    );
END;

CREATE TRIGGER product_operations_mark_reversed
AFTER INSERT ON product_operations
WHEN NEW.reverses_operation_id IS NOT NULL
BEGIN
    UPDATE product_operations
    SET status = 'REVERSED'
    WHERE id = NEW.reverses_operation_id;
END;

CREATE TRIGGER product_operations_immutable
BEFORE UPDATE ON product_operations
WHEN NOT (
    OLD.status = 'POSTED'
    AND NEW.status = 'REVERSED'
    AND NEW.id IS OLD.id
    AND NEW.organization_id IS OLD.organization_id
    AND NEW.product_id IS OLD.product_id
    AND NEW.operation_type IS OLD.operation_type
    AND NEW.quantity IS OLD.quantity
    AND NEW.operation_at IS OLD.operation_at
    AND NEW.document_ref IS OLD.document_ref
    AND NEW.comment IS OLD.comment
    AND NEW.reverses_operation_id IS OLD.reverses_operation_id
    AND NEW.reversal_reason IS OLD.reversal_reason
    AND NEW.created_at IS OLD.created_at
    AND EXISTS (
        SELECT 1
        FROM product_operations AS reversal
        WHERE reversal.reverses_operation_id = OLD.id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'posted product operations are immutable');
END;

CREATE TRIGGER product_operations_no_delete
BEFORE DELETE ON product_operations
WHEN OLD.status IN ('POSTED', 'REVERSED')
BEGIN
    SELECT RAISE(ABORT, 'posted product operations cannot be deleted');
END;
