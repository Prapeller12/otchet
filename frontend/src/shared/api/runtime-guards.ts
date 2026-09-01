import { REPORT_TYPES } from "./report-cell-contract";
import type {
  MatrixCellContract,
  ReportMatrixContract,
  SaveReportCellsResponse,
} from "./application-gateway";
import type { ReportCellContract } from "./report-cell-contract";

type JsonRecord = Record<string, unknown>;

export function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasString(value: JsonRecord, key: string): boolean {
  return typeof value[key] === "string";
}

function assertReportCell(value: unknown): asserts value is ReportCellContract {
  if (!isRecord(value) || !isRecord(value.coordinate) || !isRecord(value.value)) {
    throw new Error("Bridge returned an invalid report cell");
  }
  if (!isRecord(value.state)) {
    throw new Error("Bridge returned a report cell without state");
  }
  if (!REPORT_TYPES.includes(value.coordinate.report_type as never)) {
    throw new Error("Bridge returned an unknown report type");
  }
  if (!hasString(value.coordinate, "organization_id")) {
    throw new Error("Bridge returned a cell without organization_id");
  }
  if (value.value.kind === "QUANTITY" && typeof value.value.quantity !== "string") {
    throw new Error("Bridge quantity must be an exact decimal string");
  }
  if (
    value.value.kind !== "QUANTITY" &&
    value.value.kind !== "DATA_NOT_PROVIDED"
  ) {
    throw new Error("Bridge returned an unknown cell value kind");
  }
}

function assertMatrixCell(value: unknown): asserts value is MatrixCellContract {
  if (!isRecord(value)) {
    throw new Error("Bridge returned an invalid matrix cell");
  }
  const record: JsonRecord = value;
  assertReportCell(value);
  if (typeof record.column_id !== "string") {
    throw new Error("Bridge returned a cell without column_id");
  }
}

export function parseReportMatrix(value: unknown): ReportMatrixContract {
  if (!isRecord(value) || !REPORT_TYPES.includes(value.report_type as never)) {
    throw new Error("Bridge returned an invalid report matrix");
  }
  if (
    !hasString(value, "title") ||
    !hasString(value, "subtitle") ||
    !hasString(value, "matrix_revision") ||
    !Array.isArray(value.rows) ||
    !Array.isArray(value.left_columns) ||
    !Array.isArray(value.time_columns)
  ) {
    throw new Error("Bridge returned an incomplete report matrix");
  }
  for (const row of value.rows) {
    if (!isRecord(row) || !Array.isArray(row.cells)) {
      throw new Error("Bridge returned an invalid matrix row");
    }
    row.cells.forEach(assertMatrixCell);
  }
  return value as ReportMatrixContract;
}

export function parseSaveResponse(value: unknown): SaveReportCellsResponse {
  if (
    !isRecord(value) ||
    !hasString(value, "matrix_revision") ||
    !Array.isArray(value.cells)
  ) {
    throw new Error("Bridge returned an invalid save response");
  }
  value.cells.forEach(assertReportCell);
  return value as SaveReportCellsResponse;
}
