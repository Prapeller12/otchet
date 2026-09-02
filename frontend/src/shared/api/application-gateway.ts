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
  id: string;
  label: string;
  width: number;
};

export type MatrixTimeColumn = {
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
  issue?: CellIssue;
  lock_reason?: string;
};

export type MatrixRowContract = {
  id: string;
  group_id: string;
  group_label: string;
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

export type ReportMatrixContract = {
  report_type: ReportType;
  organization_id: string;
  title: string;
  subtitle: string;
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
  report_type: ReportType;
  organization_id: string;
};

export type CellChange = {
  coordinate: ReportCellCoordinate;
  value: ReportCellValue;
};

export type SaveReportCellsRequest = {
  report_type: ReportType;
  organization_id: string;
  base_revision: string;
  idempotency_key: string;
  changes: CellChange[];
};

export type SaveReportCellsResponse = {
  matrix_revision: string;
  cells: ReportCellContract[];
};

export type ImportRequest = {
  report_type: ReportType;
  organization_id: string;
};

export type ImportIssue = {
  source_cell: string | null;
  code: string;
  message: string;
};

export type ImportPreview = {
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
  batch_id: string;
};

export type CommitImportResult = {
  batch_id: string;
  status: "COMMITTED";
  imported_count: number;
  same_count: number;
  backup_file: string | null;
  already_committed: boolean;
};

export type ExportRequest = {
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
  id: string;
  label: string;
  group_kind: string;
};

export type ReportLayoutRow = {
  id: string | null;
  template_group_id: string;
  party_name: string;
  position_name: string;
};

export type ReportLayoutContract = {
  report_type: ReportType;
  organization_id: string;
  templates: ReportLayoutTemplate[];
  rows: ReportLayoutRow[];
};

export type ReportLayoutQuery = {
  report_type: ReportType;
  organization_id: string;
};

export type SaveReportLayoutRequest = ReportLayoutQuery & {
  rows: ReportLayoutRow[];
};

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

export interface ApplicationGateway {
  readonly mode: "pywebview" | "demo";
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
