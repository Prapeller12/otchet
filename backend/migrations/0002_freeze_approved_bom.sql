-- Preserve every approved BOM as an immutable historical record.
--
-- A draft remains editable until it is approved.  Approval freezes the
-- version identity, validity interval and item set.  An approved version may
-- only advance to RETIRED; retirement does not make it editable again.

CREATE TRIGGER bom_items_no_insert_into_frozen_version
BEFORE INSERT ON bom_items
WHEN EXISTS (
    SELECT 1
    FROM bom_versions AS version
    WHERE version.organization_id = NEW.organization_id
      AND version.id = NEW.bom_version_id
      AND version.status IN ('APPROVED', 'RETIRED')
)
BEGIN
    SELECT RAISE(ABORT, 'approved BOM items are immutable');
END;

CREATE TRIGGER bom_items_no_update_in_frozen_version
BEFORE UPDATE ON bom_items
WHEN EXISTS (
    SELECT 1
    FROM bom_versions AS version
    WHERE version.organization_id = OLD.organization_id
      AND version.id = OLD.bom_version_id
      AND version.status IN ('APPROVED', 'RETIRED')
)
OR EXISTS (
    SELECT 1
    FROM bom_versions AS version
    WHERE version.organization_id = NEW.organization_id
      AND version.id = NEW.bom_version_id
      AND version.status IN ('APPROVED', 'RETIRED')
)
BEGIN
    SELECT RAISE(ABORT, 'approved BOM items are immutable');
END;

CREATE TRIGGER bom_items_no_delete_from_frozen_version
BEFORE DELETE ON bom_items
WHEN EXISTS (
    SELECT 1
    FROM bom_versions AS version
    WHERE version.organization_id = OLD.organization_id
      AND version.id = OLD.bom_version_id
      AND version.status IN ('APPROVED', 'RETIRED')
)
BEGIN
    SELECT RAISE(ABORT, 'approved BOM items are immutable');
END;

CREATE TRIGGER bom_versions_frozen_identity
BEFORE UPDATE OF
    id,
    organization_id,
    product_id,
    version,
    valid_from,
    valid_to,
    created_at
ON bom_versions
WHEN OLD.status IN ('APPROVED', 'RETIRED')
AND (
    NEW.id IS NOT OLD.id
    OR NEW.organization_id IS NOT OLD.organization_id
    OR NEW.product_id IS NOT OLD.product_id
    OR NEW.version IS NOT OLD.version
    OR NEW.valid_from IS NOT OLD.valid_from
    OR NEW.valid_to IS NOT OLD.valid_to
    OR NEW.created_at IS NOT OLD.created_at
)
BEGIN
    SELECT RAISE(ABORT, 'approved BOM version identity is immutable');
END;

CREATE TRIGGER bom_versions_frozen_status
BEFORE UPDATE OF status ON bom_versions
WHEN (
    OLD.status = 'APPROVED'
    AND NEW.status NOT IN ('APPROVED', 'RETIRED')
)
OR (
    OLD.status = 'RETIRED'
    AND NEW.status <> 'RETIRED'
)
BEGIN
    SELECT RAISE(ABORT, 'approved BOM version cannot be reopened');
END;

CREATE TRIGGER bom_versions_no_delete_frozen
BEFORE DELETE ON bom_versions
WHEN OLD.status IN ('APPROVED', 'RETIRED')
BEGIN
    SELECT RAISE(ABORT, 'approved BOM version cannot be deleted');
END;
