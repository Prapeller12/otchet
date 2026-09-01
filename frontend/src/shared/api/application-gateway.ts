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
};

export type MatrixNavigation = {
  enter_direction: "down" | "right" | "stay";
};

export type ReportMatrixContract = {
  report_type: ReportType;
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
};

export type CellChange = {
  coordinate: ReportCellCoordinate;
  value: ReportCellValue;
};

export type SaveReportCellsRequest = {
  report_type: ReportType;
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
};

export type ImportPreview = {
  batch_id: string;
  new_count: number;
  changed_count: number;
  same_count: number;
  error_count: number;
};

export type ExportRequest = {
  report_type: ReportType;
};

export type ExportResult = {
  file_name: string;
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
  exportReport(request: ExportRequest): Promise<ExportResult>;
}
