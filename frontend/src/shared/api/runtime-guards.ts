import { REPORT_TYPES } from "./report-cell-contract";
import type {
  MatrixCellContract,
  ReportMatrixContract,
  SaveReportCellsResponse,
  SaveReportDraftResponse,
  NetworkStatus,
  NetworkOperationResult,
} from "./application-gateway";
import type { ReportCellContract } from "./report-cell-contract";

type JsonRecord = Record<string, unknown>;

export function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseNetworkStatus(value: unknown): NetworkStatus {
  if (!isRecord(value) || !["local", "network"].includes(String(value.mode))
    || !["ready", "stale", "unavailable", "outcome_unknown"].includes(String(value.state))
    || typeof value.snapshot_changed !== "boolean" || typeof value.can_write !== "boolean"
    || !["loaded_global_revision", "current_global_revision", "message", "pending_operation_id"].every(key => value[key] === null || typeof value[key] === "string")) {
    throw new Error("Не удалось распознать состояние общего хранилища.");
  }
  return value as NetworkStatus;
}

export function parseNetworkOperationResult(value: unknown, operationId: string): NetworkOperationResult {
  if (!isRecord(value) || typeof value.found !== "boolean" || value.operation_id !== operationId
    || !["committed", "not_committed", "unknown"].includes(String(value.data_state))
    || !["global_revision", "current_global_revision"].every(key => value[key] === null || typeof value[key] === "string")
    || (!value.found && (value.data_state !== "unknown" || value.result !== null))
    || (value.found && (!isRecord(value.result) || typeof value.result.ok !== "boolean" || value.data_state === "unknown"
      || (value.data_state === "committed" && (value.result.ok !== true || !("data" in value.result)))
      || (value.data_state === "not_committed" && (value.result.ok !== false || !isRecord(value.result.error) || typeof value.result.error.code !== "string" || typeof value.result.error.message !== "string"))))) {
    throw new Error("Не удалось распознать результат общей записи. Её исход остаётся неизвестным.");
  }
  return value as NetworkOperationResult;
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
    !hasString(value, "organization_id") ||
    !hasString(value, "matrix_revision") ||
    !Array.isArray(value.rows) ||
    !Array.isArray(value.left_columns) ||
    !Array.isArray(value.time_columns)
  ) {
    throw new Error("Bridge returned an incomplete report matrix");
  }
  if ("global_revision" in value && value.global_revision !== null && typeof value.global_revision !== "string") throw new Error("Bridge returned an invalid global revision");
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

export function parseDraftSaveResponse(value: unknown): SaveReportDraftResponse {
  if (!isRecord(value) || value.data_state !== "committed" || !hasString(value, "operation_revision") || !hasString(value, "current_revision") || typeof value.idempotent_replay !== "boolean" || !isRecord(value.presentation)
    || !["verified", "verification_error", "pending", "stale"].includes(String(value.verification_state))
    || !["complete", "post_backup_warning", "pending"].includes(String(value.backup_state))) {
    throw new Error("Программа вернула неполный результат сохранения черновика");
  }
  parseSaveResponse(value);
  return value as SaveReportDraftResponse;
}
