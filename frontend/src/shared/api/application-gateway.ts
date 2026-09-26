import type {
  ReportCellContract,
  ReportCellCoordinate,
  ReportCellValue,
  ReportType,
} from "./report-cell-contract";

export type FormStatus = "WORKING_REFERENCE" | "APPROVED";

export type Capability =
  | { enabled: true }
  | { enabled: false; reason: string };

export type MatrixCapabilities = {
  save: Capability;
  import: Capability;
  export: Capability;
};

export type MatrixLeftColumn = {
  shared?: boolean;
  id: string;
  label: string;
  width: number;
};

export type MatrixTimeColumn = {
  kind?: string;
  id: string;
  label: string;
  group_label: string;
  width: number;
};

export type CellIssue = {
  code: string;
  message: string;
};

export type MatrixCellContract = ReportCellContract & {
  column_id: string;
  tone?: string;
  issue?: CellIssue;
  lock_reason?: string;
  formula?: string;
};

export type MatrixRowContract = {
  manufactured_total?: string;
  workspace_id?: string; supplier_id?: string;
  stock_by_week?: Record<string, ReportCellValue>;
  archived?: boolean;
  id: string;
  group_id: string;
  group_label: string;
  category?: string;
  image?: string;
  left_values: Record<string, string>;
  cells: MatrixCellContract[];
  indicator_detail?:
    | { kind: "SUM"; label: string }
    | { kind: "CALCULATION"; label: string }
    | null;
};

export type MatrixNavigation = {
  enter_direction: "down" | "right" | "stay";
};

export type DailySummaryRow = { row_id: string; annual: string; monthly: string[]; through_month: string[] };
export type DailySummary = {
  year: number;
  periods: string[];
  rows: DailySummaryRow[];
  components: {
    group_id: string; party: string; position: string;
    rows: (DailySummaryRow & { metric_code: string })[];
  }[];
};

export type ReportMatrixContract = {
  daily_summary?: DailySummary;
  subsidiary?: boolean;
  head_site?: boolean;
  legacy_cells?: {coordinate: ReportCellCoordinate; value: ReportCellValue}[];
  year?: number | null;
  presentation?: ReportPresentation;
  report_type: ReportType;
  organization_id: string;
  title: string;
  subtitle: string;
  calendar_notice?: string;
  form_status: FormStatus;
  source_notice: string;
  matrix_revision: string;
  left_columns: MatrixLeftColumn[];
  time_columns: MatrixTimeColumn[];
  rows: MatrixRowContract[];
  capabilities: MatrixCapabilities;
  navigation: MatrixNavigation;
};

export type ReportMatrixQuery = {
  year?: number;
  preview_changes?: CellChange[];
  report_type: ReportType;
  organization_id: string;
};

export type CellChange = {
  coordinate: ReportCellCoordinate;
  value: ReportCellValue;
};

export type ConfirmationContext = { year?: number; month: number; week_start?: string };
export type SaveVerificationResult = { verification?: ReportVerification; verification_error?: { code: string; message: string } };

export type SaveReportCellsRequest = {
  confirmation?: ConfirmationContext;
  year?: number;
  report_type: ReportType;
  organization_id: string;
  base_revision: string;
  idempotency_key: string;
  changes: CellChange[];
};

export type SaveReportCellsResponse = SaveVerificationResult & {
  matrix_revision: string;
  cells: ReportCellContract[];
};

export type ImportRequest = {
  batch_id?: string;
  sheet_decisions?: Record<string, { include: boolean; reason: string }>;
  mode?: ImportMode;
  year?: number;
  report_type: ReportType;
  organization_id: string;
};

export type ImportIssue = {
  source_cell: string | null;
  code: string;
  message: string;
};

export type ImportMode = "create" | "append" | "update";
export type CanonicalSheetReview = { name: string; state: string; known?: boolean; requires_decision?: boolean; included?: boolean | null; reason?: string };
export type ImportValueChange = { source_cell: string; target: string; period: string; before: ReportCellValue | null; after: ReportCellValue | null; classification: string };
export type ImportRequisiteChange = { source_cell: string; before: unknown; after: unknown; label?: string };
export type ImportReconciliation = { source_cell: string; sheet: string; address: string; source_value: string | null; program_value: string | null; unit: string; period: string | { start: string; end: string; calendar?: string } | null; status: "MATCH" | "MISMATCH" | "UNVERIFIED"; reason: string; rounding: string };
export type ImportPreview = {
  reconciliation?: ImportReconciliation[];
  value_changes?: ImportValueChange[];
  metadata?: { structural_changes?: ImportRequisiteChange[]; sheets?: CanonicalSheetReview[]; review_sheets?: CanonicalSheetReview[]; [key: string]: unknown };
  mode?: ImportMode;
  validation_pending?: boolean;
  transfer_validated?: boolean;
  target_matrix?: ReportMatrixContract;
  applied_period_rules?: Record<string, TransferPeriodRule>;
  applied_sheet_decisions?: Record<string, { include: boolean; reason: string }>;
  profile_reused?: boolean;
  sources?: Record<string, TransferSource>;
  recognition?: ReferenceRecognition;
  auto_mappings?: TransferMapping[];
  structural_actions?: Record<string, unknown>[];
  position_count?: number;
  dictionary_count?: number;
  skipped_count?: number;
  reference_workbook?: ReferenceWorkbook;
  cancelled: boolean;
  batch_id?: string;
  file_name?: string;
  source_sha256?: string;
  status?: "STAGED" | "INVALID" | "COMMITTED";
  new_count?: number;
  changed_count?: number;
  same_count?: number;
  error_count?: number;
  already_imported?: boolean;
  issues?: ImportIssue[];
};

export type CommitImportRequest = {
  year?: number;
  batch_id: string;
};

export type CommitImportResult = {
  reference_workbook_id?: string;
  batch_id: string;
  status: "COMMITTED";
  imported_count: number;
  same_count: number;
  backup_file: string | null;
  already_committed: boolean;
};

export type ExportRequest = {
  visible_months?: string[];
  stock_weeks?: Record<string, string>;
  year?: number;
  report_type: ReportType;
  organization_id: string;
};

export type ExportResult = {
  cancelled: boolean;
  file_name?: string;
  file_path?: string;
  sha256?: string;
  exported_cell_count?: number;
};

export type OrganizationOption = {
  id: string;
  name: string;
  kind: "HEAD" | "SUBSIDIARY";
};

export type OrganizationList = {
  organizations: OrganizationOption[];
};

export type OrganizationResult = {
  organization: OrganizationOption;
};

export type ReportLayoutTemplate = {
  repeatable?: boolean;
  id: string;
  label: string;
  group_kind: string;
  indicators?: FieldIndicator[];
};

export type FieldIndicator = { code: string; label: string; formula: string };
export type FieldPreset = { label: string; required_codes: string[]; indicators: FieldIndicator[] };
export type SubsidiaryDetail = { number: string; designation: string; suppliers: { id: string; name: string; contract: string; archived: boolean }[] };
export type FieldConfiguration = {
  subsidiary?: SubsidiaryDetail;
  head_links?: string[];
  category: string;
  image: string;
  norm: string;
  opening: string;
  opening_date?: string;
  indicators: FieldIndicator[];
};

export type ReportLayoutRow = {
  id: string | null;
  template_group_id: string;
  party_name: string;
  position_name: string;
  configuration?: FieldConfiguration;
};

export type ReportLayoutContract = {
  report_type: ReportType;
  organization_id: string;
  templates: ReportLayoutTemplate[];
  rows: ReportLayoutRow[];
  presets?: FieldPreset[];
};

export type ReportLayoutQuery = {
  report_type: ReportType;
  organization_id: string;
};

export type SaveReportLayoutRequest = ReportLayoutQuery & {
  rows: ReportLayoutRow[];
};

export type ReportHeaderFields = { product_designation: string; product_name: string; factory_name: string; product_image: string };
export type ProductionCode = { id: string; label: string; plans: Record<string, string>; actuals: Record<string, string> };
export type ReportPresentation = SaveVerificationResult & {
  title?: string; widths?: Record<string, number>; plans?: Record<string, string>;
  actuals?: Record<string, string>; completion?: Record<string, string>;
  header?: ReportHeaderFields; production_codes?: ProductionCode[];
  production_code_annual?: Record<string, string>;
  annual?: { plan: string; actual: string; completion: string; plan_months: number; actual_months: number };
};
export type SaveReportPresentationRequest = ReportLayoutQuery & ReportPresentation & { confirmation?: ConfirmationContext; expected_revision?: string; confirm_production_totals?: boolean; production_code_actuals?: Record<string, Record<string, string>> };

export type ApplicationError = {
  code: string;
  message: string;
  request_id?: string;
  field_errors?: ReadonlyArray<{
    field: string;
    code: string;
    message: string;
  }>;
};

export type UiPreferences = { field_hints_enabled: boolean };

export interface ApplicationGateway {
  listRecoveryBackups?(): Promise<{ backups: RecoveryBackup[] }>;
  verifyRecoveryBackup?(backupId: string): Promise<{ valid: boolean; created_at: string }>;
  restoreRecoveryBackup?(backupId: string): Promise<RecoveryResult>;
  getUiPreferences?(): Promise<UiPreferences>;
  saveUiPreferences?(request: UiPreferences): Promise<UiPreferences>;
  referenceReport?(request: ReferenceRequest): Promise<unknown>;
  exportPdf?(request: MonthlyReportQuery): Promise<ExportResult>;
  listReportSigners?(): Promise<ReportSigner[]>;
  manageReportSigner?(request: ManageReportSignerRequest): Promise<ManageReportSignerResult>;
  createReportSigner?(request: CreateReportSignerRequest): Promise<ReportSigner>;
  getReportVerification?(request: MonthlyReportQuery): Promise<ReportVerification>;
  verifyReport?(request: VerifyReportRequest): Promise<ReportVerification>;
  getAccessStatus?(): Promise<AccessStatus>;
  setupAccess?(request: { display_name?: string; pin: string; signer_id?: string }): Promise<AccessStatus>;
  unlockAccess?(authorization: WriteAuthorization): Promise<AccessStatus>;
  enrollAccess?(authorization: WriteAuthorization): Promise<AccessUser>;
  authenticateAccess?(authorization: WriteAuthorization): Promise<AccessUser>;
  endAdministration?(): Promise<void>;
  setAuthorizationHandler?(handler: AuthorizationHandler | null): void;
  readonly mode: "pywebview" | "demo";
  saveReportPresentation?(request: SaveReportPresentationRequest): Promise<ReportPresentation>;
  getReportMatrix(query: ReportMatrixQuery): Promise<ReportMatrixContract>;
  saveReportCells(
    request: SaveReportCellsRequest,
  ): Promise<SaveReportCellsResponse>;
  validateImport(request: ImportRequest): Promise<ImportPreview>;
  commitImport(request: CommitImportRequest): Promise<CommitImportResult>;
  exportReport(request: ExportRequest): Promise<ExportResult>;
  listOrganizations(): Promise<OrganizationList>;
  createOrganization(name: string): Promise<OrganizationResult>;
  renameOrganization(organizationId: string, name: string): Promise<OrganizationResult>;
  archiveOrganization(organizationId: string): Promise<OrganizationList>;
  getReportLayout(query: ReportLayoutQuery): Promise<ReportLayoutContract>;
  saveReportLayout(request: SaveReportLayoutRequest): Promise<ReportLayoutContract>;
}

export type MonthlyReportQuery = ExportRequest & { month: number; week_start?: string; expected_revision?: string };
export type ReportVerification = {
  status: "UNVERIFIED" | "VERIFIED" | "STALE" | "LEGACY" | "INVALID";
  snapshot_sha256: string; verified_sha256?: string;
  signer_name?: string; signed_at?: string;
  signer_id?: string; key_fingerprint?: string; public_key?: string; signature?: string; algorithm?: "Ed25519";
};
export type VerifyReportRequest = MonthlyReportQuery & { signer_id: string; pin: string; snapshot_sha256: string; confirmed: boolean };

export type TransferSource = {
  sheet: string; address: string; value: string; formula?: string; role: string; unit?: string;
  formula_status?: string; number_format?: string; metric?: string | null;
  period?: { start: string; end: string; calendar: string } | null;
  position?: { code: string; name: string; structure_number: string; parent_code: string; norm: string; manufacturer: string };
  errors?: { code: string; message: string }[]; coordinate?: ReportCellCoordinate;
};
export type TransferMapping = { source: string; coordinate?: ReportCellCoordinate; quantity?: string; confirmed?: boolean; skip_reason?: string; method?: string };
export type TransferStructureOverride = { code?: string; name?: string; manufacturer?: string; norm?: string; parent_code?: string; reason: string };
export type TransferPeriodRule = { start: string; end: string; calendar: "USER_CONFIRMED"; reason: string };
export type TransferPeriodBlock = { key: string; sheet: string; address: string; label: string; year: number | null; month: number | null; week_label?: string; errors?: { code: string; message: string }[] };
export type ReferenceRecognition = { period_blocks?: TransferPeriodBlock[]; profile_id?: string; profile_version?: string | number; structure_fingerprint?: string; sources?: Record<string, TransferSource>; fields?: Record<string, TransferSource>; structural_actions?: Record<string, unknown>[]; sheets?: { index: number; name: string; state: string; decision_required: boolean }[]; issues?: ImportIssue[] };
export type ReferenceCell = { value: string | null; kind: "n" | "s" | "f" | "d"; display: string; error?: string };
export type ReferenceSheet = { name: string; rows: number; columns: number; merges: string[]; cells: Record<string, ReferenceCell> };
export type ReferenceWorkbook = { id: string; file_name: string; report_type: ReportType; revision: number; warnings: string[]; errors: string[]; sheets: ReferenceSheet[]; recognition?: ReferenceRecognition };
export type ReferenceSummary = Pick<ReferenceWorkbook, "id" | "file_name" | "report_type">;
export type ReferenceRequest = {
  report_type?: ReportType; year?: number; mode?: ImportMode; period_rules?: Record<string, TransferPeriodRule>; sheet_decisions?: Record<string, { include: boolean; reason: string }>; structure_overrides?: Record<string, TransferStructureOverride>; mappings?: TransferMapping[]; action: "list" | "get" | "save" | "export" | "transfer"; organization_id: string; id?: string; revision?: number; changes?: { sheet: number; address: string; value: string | null }[] };

export type ReportSigner = { revoked?: boolean; can_unlock?: boolean; id: string; display_name: string; role: AccessRole; key_fingerprint: string; created_at: string };
export type CreateReportSignerRequest = { display_name: string; pin: string; role?: AccessRole; admin_id?: string; admin_pin?: string };

export type AccessRole = "admin" | "reviewer" | "project_manager";
export type AccessUser = { revoked?: boolean; can_unlock?: boolean; id: string; display_name: string; role: AccessRole };
export type AccessStatus = { state: "setup" | "legacy" | "locked" | "ready"; users: AccessUser[]; current_user?: AccessUser | null; automatic_open_available?: boolean; automatic_open_error?: string };
export type WriteAuthorization = { signer_id: string; pin: string };
export type AuthorizationPrompt = { title: string; adminOnly: boolean; confirmLabel?: string };
export type AuthorizationHandler = (prompt: AuthorizationPrompt, execute: (authorization: WriteAuthorization) => Promise<unknown>) => Promise<unknown>;

export type ManageReportSignerRequest = { action: "change_pin" | "revoke" | "transfer_admin"; signer_id: string; current_pin?: string; new_pin?: string };
export type ManageReportSignerResult = { users: ReportSigner[]; administrator_changed: boolean; access_warning?: string | null };

export type RecoveryBackup = { backup_id: string; path: string; valid: boolean; created_at?: string; application_version?: string; encrypted?: boolean; error?: string };
export type RecoveryResult = { cancelled: boolean; directory?: string; database_path?: string; instructions?: string[] };
