CREATE TABLE report_presentation (
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    report_type TEXT NOT NULL CHECK (report_type IN ('DAILY_MOVEMENT', 'HEAD_SITE', 'SUBSIDIARY')),
    settings_json TEXT NOT NULL CHECK (json_valid(settings_json)),
    PRIMARY KEY (organization_id, report_type)
) STRICT;

-- Preserve the meaning of existing monthly opening balances during the upgrade.
UPDATE report_workspace_groups
SET configuration_json = json_set(configuration_json, '$.opening_date', strftime('%Y-%m-01', 'now'))
WHERE coalesce(json_extract(configuration_json, '$.opening'), '') <> ''
  AND json_extract(configuration_json, '$.opening_date') IS NULL;

-- Upgrade only the exact pair installed by the previous readiness preset.
UPDATE report_workspace_groups
SET configuration_json = json_set(configuration_json,
    '$.indicators[' || (
        SELECT item.key FROM json_each(configuration_json, '$.indicators') AS item
        WHERE json_extract(item.value, '$.code') = 'WRK_DAILY_BALANCE'
        LIMIT 1
    ) || '].formula', '=BALANCE(WRK_DAILY_RECEIVED,WRK_DAILY_USED)')
WHERE EXISTS (
    SELECT 1 FROM json_each(configuration_json, '$.indicators') AS item
    WHERE json_extract(item.value, '$.code') = 'WRK_DAILY_BALANCE'
      AND json_extract(item.value, '$.formula') = '=OPENING+CUM(WRK_DAILY_RECEIVED)-CUM(WRK_DAILY_USED)'
)
AND EXISTS (
    SELECT 1 FROM json_each(configuration_json, '$.indicators') AS item
    WHERE json_extract(item.value, '$.code') = 'READY_SETS'
      AND json_extract(item.value, '$.formula') = '=IF(WRK_DAILY_BALANCE>=NORM,ROUNDDOWN(WRK_DAILY_BALANCE/NORM,0),0)'
);
