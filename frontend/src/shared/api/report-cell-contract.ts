/**
 * Transport-neutral application contract for a matrix report cell.
 *
 * Runtime validation is defined by
 * resources/schemas/report-cell/report-cell.schema.json. These aliases stay
 * plain strings because the wire format is JSON; consumers must not infer
 * database keys or business meaning from identifiers.
 */
export type OpaqueId = string;
export type ContractCode = string;
export type IsoDate = string;
export type DecimalString = string;

export const REPORT_TYPES = [
  "DAILY_MOVEMENT",
  "HEAD_SITE",
  "SUBSIDIARY",
] as const;

export type ReportType = (typeof REPORT_TYPES)[number];

export type ReportCellSubjectCoordinate =
  | { product_id: OpaqueId; component_id?: never }
  | { component_id: OpaqueId; product_id?: never };

export type ReportCellIndicatorCoordinate =
  | { metric_code: ContractCode; operation_type?: never }
  | { operation_type: ContractCode; metric_code?: never };

export type ReportCellTimeCoordinate =
  | { operation_date: IsoDate; period_start?: never }
  | { period_start: IsoDate; operation_date?: never };

export type ReportCellCoordinate = {
  report_type: ReportType;
  organization_id: OpaqueId;
  bom_version_id?: OpaqueId;
} &
  ReportCellSubjectCoordinate &
  ReportCellIndicatorCoordinate &
  ReportCellTimeCoordinate;

export type DataNotProvidedValue = {
  kind: "DATA_NOT_PROVIDED";
};

export type QuantityValue = {
  kind: "QUANTITY";
  quantity: DecimalString;
};

export type ReportCellValue = DataNotProvidedValue | QuantityValue;

export const REPORT_CELL_ACCESS_STATES = [
  "editable",
  "calculated",
  "locked",
] as const;

export type ReportCellAccessState =
  (typeof REPORT_CELL_ACCESS_STATES)[number];

export const REPORT_CELL_PERSISTENCE_STATES = [
  "error",
  "dirty",
  "saving",
  "saved",
] as const;

export type ReportCellPersistenceState =
  (typeof REPORT_CELL_PERSISTENCE_STATES)[number];

export type ReportCellState = {
  access: ReportCellAccessState;
  persistence: ReportCellPersistenceState;
};

export type ReportCellContract = {
  coordinate: ReportCellCoordinate;
  value: ReportCellValue;
  state: ReportCellState;
};
