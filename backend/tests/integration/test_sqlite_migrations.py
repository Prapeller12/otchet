from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from backend.infrastructure.database.migrator import (
    MigrationError,
    apply_migrations,
    connect_sqlite,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture
def database(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    connection = connect_sqlite(tmp_path / "reporting.db")
    apply_migrations(connection, MIGRATIONS)
    try:
        yield connection
    finally:
        connection.close()


def _seed_scope(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO organizations (id, code, name) VALUES (1, 'ORG-1', 'Organization 1')"
    )
    connection.execute(
        "INSERT INTO organizations (id, code, name) VALUES (2, 'ORG-2', 'Organization 2')"
    )
    connection.execute(
        """
        INSERT INTO products (id, organization_id, code, name)
        VALUES (10, 1, 'PRODUCT-1', 'Product 1')
        """
    )
    connection.execute(
        """
        INSERT INTO components (id, organization_id, code, name, kind)
        VALUES (20, 1, 'COMPONENT-1', 'Component 1', 'UNSPECIFIED')
        """
    )
    connection.execute(
        """
        INSERT INTO components (id, organization_id, code, name, kind)
        VALUES (21, 2, 'COMPONENT-2', 'Component 2', 'UNSPECIFIED')
        """
    )
    connection.commit()


def test_migrations_apply_to_empty_database_and_are_idempotent(tmp_path: Path) -> None:
    connection = connect_sqlite(tmp_path / "empty.db")
    try:
        assert apply_migrations(connection, MIGRATIONS) == (
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
        )
        assert apply_migrations(connection, MIGRATIONS) == ()

        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "schema_migrations",
            "organizations",
            "products",
            "components",
            "bom_versions",
            "bom_items",
            "stock_operations",
            "product_operations",
            "report_fact_revisions",
            "idempotency_records",
            "audit_events",
            "report_workspace_profiles",
            "report_workspace_groups",
            "import_batches",
            "import_rows",
            "import_errors",
        } <= tables
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "quantity",
    ["", "0", "0.0", "-1", "+1", "01", ".5", "5.", "1e3", "1,5", "1.2.3"],
)
def test_operation_quantity_rejects_non_positive_or_non_plain_decimal(
    database: sqlite3.Connection,
    quantity: str,
) -> None:
    _seed_scope(database)

    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            """
            INSERT INTO stock_operations (
                organization_id, component_id, operation_type, quantity, operation_at
            ) VALUES (1, 20, 'TEST_OPERATION', ?, '2026-08-30T09:00:00Z')
            """,
            (quantity,),
        )


def test_exact_quantity_is_stored_as_text_without_float_conversion(
    database: sqlite3.Connection,
) -> None:
    _seed_scope(database)
    exact_value = "12345678901234567890.12345678901234567890123456789"

    database.execute(
        """
        INSERT INTO stock_operations (
            organization_id, component_id, operation_type, quantity, operation_at
        ) VALUES (1, 20, 'TEST_OPERATION', ?, '2026-08-30T09:00:00Z')
        """,
        (exact_value,),
    )

    stored = database.execute("SELECT quantity, typeof(quantity) FROM stock_operations").fetchone()
    assert stored == (exact_value, "text")


@pytest.mark.parametrize("quantity", ["1.25", "-1.25"])
def test_signed_adjustment_accepts_an_explicit_non_zero_sign(
    database: sqlite3.Connection,
    quantity: str,
) -> None:
    _seed_scope(database)

    database.execute(
        """
        INSERT INTO stock_operations (
            organization_id, component_id, operation_type, quantity, operation_at
        ) VALUES (1, 20, 'SIGNED_ADJUSTMENT', ?, '2026-08-30T09:00:00+03:00')
        """,
        (quantity,),
    )

    assert database.execute("SELECT quantity FROM stock_operations").fetchone() == (quantity,)


@pytest.mark.parametrize("quantity", ["-0", "-0.0", "--1", "-01", "-.5", "-5."])
def test_signed_adjustment_rejects_zero_or_malformed_decimal(
    database: sqlite3.Connection,
    quantity: str,
) -> None:
    _seed_scope(database)

    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            """
            INSERT INTO stock_operations (
                organization_id, component_id, operation_type, quantity, operation_at
            ) VALUES (1, 20, 'SIGNED_ADJUSTMENT', ?, '2026-08-30T09:00:00+03:00')
            """,
            (quantity,),
        )


def test_foreign_keys_checks_and_organization_scope_are_enforced(
    database: sqlite3.Connection,
) -> None:
    _seed_scope(database)

    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            """
            INSERT INTO stock_operations (
                organization_id, component_id, operation_type, quantity, operation_at
            ) VALUES (1, 999, 'TEST_OPERATION', '1', '2026-08-30T09:00:00Z')
            """
        )

    database.execute(
        """
        INSERT INTO bom_versions (
            id, organization_id, product_id, version, valid_from, status
        ) VALUES (30, 1, 10, 'v1', '2026-01-01', 'DRAFT')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            """
            INSERT INTO bom_items (
                organization_id, bom_version_id, component_id, qty_per_product
            ) VALUES (1, 30, 21, '1')
            """
        )

    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            """
            INSERT INTO stock_operations (
                organization_id, component_id, operation_type, quantity, operation_at
            ) VALUES (1, 20, 'TEST_OPERATION', '1', '2026-02-30T09:00:00Z')
            """
        )


@pytest.mark.parametrize(
    "operation_at",
    [
        "2026-08-30",
        "2026-08-30T09:00:00",
        "2026-08-30 09:00:00Z",
        "2026-02-30T09:00:00Z",
        "2026-08-30T24:00:00Z",
        "2026-08-30T09:00:00+24:00",
        "2026-08-30T09:00:00+14:30",
        "2026-08-30T09:00:00+03:60",
    ],
)
def test_operation_timestamp_requires_valid_iso_seconds_and_timezone(
    database: sqlite3.Connection,
    operation_at: str,
) -> None:
    _seed_scope(database)

    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            """
            INSERT INTO stock_operations (
                organization_id, component_id, operation_type, quantity, operation_at
            ) VALUES (1, 20, 'TEST_OPERATION', '1', ?)
            """,
            (operation_at,),
        )


def test_posted_operation_is_immutable_and_reversal_is_linked(
    database: sqlite3.Connection,
) -> None:
    _seed_scope(database)
    original_id = database.execute(
        """
        INSERT INTO stock_operations (
            organization_id, component_id, operation_type, quantity, operation_at
        ) VALUES (1, 20, 'TEST_RECEIPT', '7.50', '2026-08-29T09:00:00+03:00')
        """
    ).lastrowid

    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        database.execute("DELETE FROM stock_operations WHERE id = ?", (original_id,))

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        database.execute("UPDATE stock_operations SET quantity = '8' WHERE id = ?", (original_id,))

    with pytest.raises(sqlite3.IntegrityError, match="exactly match"):
        database.execute(
            """
            INSERT INTO stock_operations (
                organization_id,
                component_id,
                operation_type,
                quantity,
                operation_at,
                reverses_operation_id,
                reversal_reason
            ) VALUES (
                1, 20, 'TEST_RECEIPT', '8', '2026-08-30T08:00:00+03:00', ?,
                'Wrong quantity'
            )
            """,
            (original_id,),
        )

    reversal_id = database.execute(
        """
        INSERT INTO stock_operations (
            organization_id,
            component_id,
            operation_type,
            quantity,
            operation_at,
            reverses_operation_id,
            reversal_reason
        ) VALUES (
            1, 20, 'TEST_RECEIPT', '7.50', '2026-08-30T09:00:00+03:00', ?, 'Correction'
        )
        """,
        (original_id,),
    ).lastrowid
    assert reversal_id is not None
    statuses = database.execute("SELECT id, status FROM stock_operations ORDER BY id").fetchall()
    assert statuses == [(original_id, "REVERSED"), (reversal_id, "POSTED")]

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        database.execute(
            "UPDATE stock_operations SET comment = 'changed' WHERE id = ?", (reversal_id,)
        )

    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            """
            INSERT INTO stock_operations (
                organization_id,
                component_id,
                operation_type,
                quantity,
                operation_at,
                reverses_operation_id,
                reversal_reason
            ) VALUES (
                1, 20, 'TEST_RECEIPT', '7.50', '2026-08-30T10:00:00+03:00', ?, 'Duplicate'
            )
            """,
            (original_id,),
        )

    with pytest.raises(sqlite3.IntegrityError, match="exactly match"):
        database.execute(
            """
            INSERT INTO stock_operations (
                organization_id,
                component_id,
                operation_type,
                quantity,
                operation_at,
                reverses_operation_id,
                reversal_reason
            ) VALUES (
                1, 20, 'TEST_RECEIPT', '7.50', '2026-08-31T09:00:00+03:00', ?,
                'Invalid chain'
            )
            """,
            (reversal_id,),
        )


def test_product_operation_uses_the_same_immutable_reversal_workflow(
    database: sqlite3.Connection,
) -> None:
    _seed_scope(database)
    original_id = database.execute(
        """
        INSERT INTO product_operations (
            organization_id, product_id, operation_type, quantity, operation_at
        ) VALUES (1, 10, 'TEST_PRODUCTION', '3', '2026-08-29T09:00:00Z')
        """
    ).lastrowid

    reversal_id = database.execute(
        """
        INSERT INTO product_operations (
            organization_id,
            product_id,
            operation_type,
            quantity,
            operation_at,
            reverses_operation_id,
            reversal_reason
        ) VALUES (1, 10, 'TEST_PRODUCTION', '3', '2026-08-30T09:00:00Z', ?, 'Correction')
        """,
        (original_id,),
    ).lastrowid

    assert database.execute(
        "SELECT status FROM product_operations WHERE id = ?", (original_id,)
    ).fetchone() == ("REVERSED",)
    assert database.execute(
        "SELECT status FROM product_operations WHERE id = ?", (reversal_id,)
    ).fetchone() == ("POSTED",)

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        database.execute(
            """
            UPDATE product_operations
            SET operation_at = '2026-08-31T09:00:00Z'
            WHERE id = ?
            """,
            (original_id,),
        )


@pytest.mark.parametrize("table", ["stock_operations", "product_operations"])
def test_operation_cannot_be_inserted_directly_as_reversed(
    database: sqlite3.Connection,
    table: str,
) -> None:
    _seed_scope(database)
    foreign_key_column = "component_id" if table == "stock_operations" else "product_id"
    foreign_key_value = 20 if table == "stock_operations" else 10

    with pytest.raises(sqlite3.IntegrityError, match="must be posted"):
        database.execute(
            f"""
            INSERT INTO {table} (
                organization_id,
                {foreign_key_column},
                operation_type,
                quantity,
                operation_at,
                status
            ) VALUES (1, ?, 'TEST_OPERATION', '1', '2026-08-30T09:00:00Z', 'REVERSED')
            """,
            (foreign_key_value,),
        )


def test_approved_bom_periods_cannot_overlap(database: sqlite3.Connection) -> None:
    _seed_scope(database)
    database.execute(
        """
        INSERT INTO bom_versions (
            id, organization_id, product_id, version, valid_from, valid_to, status
        ) VALUES (30, 1, 10, 'v1', '2026-01-01', '2026-01-31', 'APPROVED')
        """
    )
    database.execute(
        """
        INSERT INTO bom_versions (
            id, organization_id, product_id, version, valid_from, valid_to, status
        ) VALUES (31, 1, 10, 'v2', '2026-01-15', '2026-02-15', 'DRAFT')
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="must not overlap"):
        database.execute("UPDATE bom_versions SET status = 'APPROVED' WHERE id = 31")

    database.execute(
        """
        INSERT INTO bom_versions (
            id, organization_id, product_id, version, valid_from, valid_to, status
        ) VALUES (32, 1, 10, 'v3', '2026-02-01', NULL, 'APPROVED')
        """
    )


def test_approved_bom_items_are_historically_immutable(
    database: sqlite3.Connection,
) -> None:
    _seed_scope(database)
    database.execute(
        """
        INSERT INTO components (id, organization_id, code, name, kind)
        VALUES (22, 1, 'COMPONENT-2', 'Component 2', 'UNSPECIFIED')
        """
    )
    database.execute(
        """
        INSERT INTO bom_versions (
            id, organization_id, product_id, version, valid_from, status
        ) VALUES (30, 1, 10, 'v1', '2026-01-01', 'DRAFT')
        """
    )
    database.execute(
        """
        INSERT INTO bom_items (
            organization_id, bom_version_id, component_id, qty_per_product
        ) VALUES (1, 30, 20, '3')
        """
    )
    database.execute(
        """
        UPDATE bom_items
        SET qty_per_product = '3.5'
        WHERE organization_id = 1 AND bom_version_id = 30 AND component_id = 20
        """
    )
    database.execute("UPDATE bom_versions SET status = 'APPROVED' WHERE id = 30")

    with pytest.raises(sqlite3.IntegrityError, match="BOM items are immutable"):
        database.execute(
            """
            INSERT INTO bom_items (
                organization_id, bom_version_id, component_id, qty_per_product
            ) VALUES (1, 30, 22, '1')
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="BOM items are immutable"):
        database.execute(
            """
            UPDATE bom_items
            SET qty_per_product = '4'
            WHERE organization_id = 1 AND bom_version_id = 30 AND component_id = 20
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="BOM items are immutable"):
        database.execute(
            """
            DELETE FROM bom_items
            WHERE organization_id = 1 AND bom_version_id = 30 AND component_id = 20
            """
        )

    assert database.execute(
        """
        SELECT component_id, qty_per_product
        FROM bom_items
        WHERE organization_id = 1 AND bom_version_id = 30
        """
    ).fetchall() == [(20, "3.5")]

    database.execute("UPDATE bom_versions SET status = 'RETIRED' WHERE id = 30")
    with pytest.raises(sqlite3.IntegrityError, match="BOM items are immutable"):
        database.execute(
            """
            UPDATE bom_items
            SET loss_factor = '0.1'
            WHERE organization_id = 1 AND bom_version_id = 30 AND component_id = 20
            """
        )


def test_approved_bom_version_identity_cannot_be_rewritten_or_deleted(
    database: sqlite3.Connection,
) -> None:
    _seed_scope(database)
    database.execute(
        """
        INSERT INTO products (id, organization_id, code, name)
        VALUES (11, 1, 'PRODUCT-2', 'Product 2')
        """
    )
    database.execute(
        """
        INSERT INTO bom_versions (
            id, organization_id, product_id, version, valid_from, valid_to, status
        ) VALUES (30, 1, 10, 'v1', '2026-01-01', '2026-12-31', 'APPROVED')
        """
    )

    key_rewrites = (
        "UPDATE bom_versions SET id = 31 WHERE id = 30",
        "UPDATE bom_versions SET product_id = 11 WHERE id = 30",
        "UPDATE bom_versions SET version = 'v2' WHERE id = 30",
        "UPDATE bom_versions SET valid_from = '2026-02-01' WHERE id = 30",
        "UPDATE bom_versions SET valid_to = NULL WHERE id = 30",
        "UPDATE bom_versions SET created_at = '2026-01-01T00:00:00Z' WHERE id = 30",
    )
    for statement in key_rewrites:
        with pytest.raises(sqlite3.IntegrityError, match="version identity is immutable"):
            database.execute(statement)

    with pytest.raises(sqlite3.IntegrityError, match="version cannot be deleted"):
        database.execute("DELETE FROM bom_versions WHERE id = 30")

    database.execute("UPDATE bom_versions SET status = 'RETIRED' WHERE id = 30")
    with pytest.raises(sqlite3.IntegrityError, match="version cannot be reopened"):
        database.execute("UPDATE bom_versions SET status = 'DRAFT' WHERE id = 30")
    with pytest.raises(sqlite3.IntegrityError, match="version identity is immutable"):
        database.execute("UPDATE bom_versions SET version = 'v2' WHERE id = 30")


def test_failed_unit_of_work_rolls_back_all_business_changes(
    database: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with database:
            database.execute(
                "INSERT INTO organizations (id, code, name) VALUES (1, 'ORG-1', 'First')"
            )
            database.execute(
                "INSERT INTO organizations (id, code, name) VALUES (2, 'ORG-1', 'Duplicate')"
            )

    count = database.execute("SELECT count(*) FROM organizations").fetchone()
    assert count == (0,)


def test_failed_migration_is_atomic(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_broken.sql").write_text(
        """
        CREATE TABLE must_be_rolled_back (id INTEGER PRIMARY KEY) STRICT;
        CREATE TABLE invalid SQL;
        """,
        encoding="utf-8",
    )
    connection = connect_sqlite(tmp_path / "atomic.db")
    try:
        with pytest.raises(sqlite3.Error):
            apply_migrations(connection, migrations)

        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'must_be_rolled_back'"
            ).fetchone()
            is None
        )
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone() == (0,)
    finally:
        connection.close()


def test_modified_applied_migration_is_rejected(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    migration = migrations / "0001_example.sql"
    migration.write_text(
        "CREATE TABLE example (id INTEGER PRIMARY KEY) STRICT;\n", encoding="utf-8"
    )
    connection = connect_sqlite(tmp_path / "checksum.db")
    try:
        assert apply_migrations(connection, migrations) == ("0001",)
        migration.write_text(
            "CREATE TABLE example (id INTEGER PRIMARY KEY) STRICT;\n-- modified\n",
            encoding="utf-8",
        )
        with pytest.raises(MigrationError, match="has been modified"):
            apply_migrations(connection, migrations)
    finally:
        connection.close()


def test_non_prefix_migration_history_is_rejected(tmp_path: Path) -> None:
    connection = connect_sqlite(tmp_path / "non-prefix.db")
    try:
        assert apply_migrations(connection, MIGRATIONS) == (
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
        )
        connection.execute("DELETE FROM schema_migrations WHERE version = '0001'")
        connection.commit()

        with pytest.raises(MigrationError, match="not a contiguous prefix"):
            apply_migrations(connection, MIGRATIONS)
    finally:
        connection.close()
