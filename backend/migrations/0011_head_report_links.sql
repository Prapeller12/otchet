-- One subsidiary production report can belong to only one head component.
CREATE TABLE head_report_links (
    subsidiary_organization_id INTEGER PRIMARY KEY REFERENCES organizations(id),
    head_group_id INTEGER NOT NULL REFERENCES report_workspace_groups(id)
);
CREATE TRIGGER head_report_links_check_insert
BEFORE INSERT ON head_report_links
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM report_workspace_groups
        WHERE id=NEW.head_group_id AND report_type='HEAD_SITE' AND is_active=1
    ) THEN RAISE(ABORT, 'Invalid head report group') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM report_workspace_profiles p JOIN organizations o ON o.id=p.organization_id
        WHERE p.organization_id=NEW.subsidiary_organization_id
        AND p.workspace_kind='SUBSIDIARY' AND o.is_active=1
    ) THEN RAISE(ABORT, 'Invalid subsidiary report') END;
END;
