-- User-owned position metadata and versioned-by-audit worksheet configuration.
ALTER TABLE report_workspace_groups ADD COLUMN configuration_json TEXT NOT NULL
    DEFAULT '{}' CHECK (json_valid(configuration_json));
