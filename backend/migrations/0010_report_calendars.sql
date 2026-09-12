-- Fix a calendar version per workspace/year so existing fact dates are never reinterpreted.
CREATE TABLE report_calendars (
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    report_type TEXT NOT NULL CHECK (report_type IN ('HEAD_SITE', 'SUBSIDIARY')),
    year INTEGER NOT NULL CHECK (year BETWEEN 1900 AND 9999),
    mode TEXT NOT NULL CHECK (mode IN ('LEGACY', 'CALENDAR_WEEKS')),
    PRIMARY KEY (organization_id, report_type, year)
) STRICT;
CREATE TABLE reference_transfer_decisions (
    batch_id TEXT PRIMARY KEY REFERENCES import_batches(id),
    workbook_id TEXT NOT NULL REFERENCES reference_workbooks(id),
    decisions TEXT NOT NULL CHECK (json_valid(decisions)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
CREATE TRIGGER reference_transfer_decisions_no_update BEFORE UPDATE ON reference_transfer_decisions
BEGIN SELECT RAISE(ABORT, 'transfer decisions are immutable'); END;
CREATE TRIGGER reference_transfer_decisions_no_delete BEFORE DELETE ON reference_transfer_decisions
BEGIN SELECT RAISE(ABORT, 'transfer decisions are immutable'); END;
