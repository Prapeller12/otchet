import type { ReportCellValue } from "../../shared/api/report-cell-contract";

const DECIMAL_PATTERN = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;
const ZERO_PATTERN = /^-?0(?:\.0+)?$/;

export type ParsedDraft =
  | { valid: true; value: ReportCellValue }
  | { valid: false; message: string };

export function parseCellDraft(draft: string): ParsedDraft {
  const trimmed = draft.trim();
  if (trimmed === "") {
    return { valid: true, value: { kind: "DATA_NOT_PROVIDED" } };
  }
  if (!DECIMAL_PATTERN.test(trimmed)) {
    return {
      valid: false,
      message: "Введите число с точкой как разделителем или оставьте ячейку пустой.",
    };
  }
  return { valid: true, value: { kind: "QUANTITY", quantity: trimmed } };
}
export function isConfirmedZero(value: ReportCellValue): boolean {
  return value.kind === "QUANTITY" && ZERO_PATTERN.test(value.quantity);
}

export function inputValue(value: ReportCellValue): string {
  return value.kind === "QUANTITY" ? value.quantity : "";
}

export function displayValue(value: ReportCellValue): string {
  return value.kind === "QUANTITY" ? value.quantity : "—";
}
