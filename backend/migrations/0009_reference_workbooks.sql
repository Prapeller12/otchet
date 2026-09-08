CREATE TABLE reference_workbooks (
 id TEXT PRIMARY KEY,
 organization_id INTEGER NOT NULL REFERENCES organizations(id),
 report_type TEXT NOT NULL CHECK(report_type IN ('HEAD_SITE','SUBSIDIARY')),
 file_name TEXT NOT NULL,
 sha256 TEXT NOT NULL,
 original BLOB NOT NULL,
 document TEXT NOT NULL CHECK(json_valid(document)),
 committed INTEGER NOT NULL DEFAULT 0 CHECK(committed IN (0,1)),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 UNIQUE(organization_id, sha256)
) STRICT;
CREATE TABLE reference_workbook_revisions (
 id INTEGER PRIMARY KEY,
 workbook_id TEXT NOT NULL REFERENCES reference_workbooks(id),
 revision INTEGER NOT NULL,
 changes TEXT NOT NULL CHECK(json_valid(changes)),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 UNIQUE(workbook_id,revision)
) STRICT;
CREATE TRIGGER reference_revisions_no_update BEFORE UPDATE ON reference_workbook_revisions
BEGIN SELECT RAISE(ABORT,'Workbook revisions are immutable'); END;
CREATE TRIGGER reference_revisions_no_delete BEFORE DELETE ON reference_workbook_revisions
BEGIN SELECT RAISE(ABORT,'Workbook revisions are immutable'); END;
CREATE TRIGGER reference_original_immutable BEFORE UPDATE OF organization_id,report_type,file_name,sha256,original,document ON reference_workbooks
BEGIN SELECT RAISE(ABORT,'Original workbook is immutable'); END;
CREATE TRIGGER reference_committed_no_delete BEFORE DELETE ON reference_workbooks WHEN OLD.committed=1
BEGIN SELECT RAISE(ABORT,'Committed workbook is immutable'); END;
