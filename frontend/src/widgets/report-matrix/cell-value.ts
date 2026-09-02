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

export function sumCellValues(
  values: ReadonlyArray<ReportCellValue>,
): string | null {
  const quantities = values.flatMap((value) =>
    value.kind === "QUANTITY" ? [value.quantity] : [],
  );
  if (quantities.length === 0) return null;
  const scale = Math.max(
    ...quantities.map((value) => value.split(".")[1]?.length ?? 0),
  );
  const total = quantities.reduce((sum, value) => {
    const negative = value.startsWith("-");
    const unsigned = negative ? value.slice(1) : value;
    const [whole = "0", fraction = ""] = unsigned.split(".");
    const scaled = BigInt(`${whole}${fraction.padEnd(scale, "0")}`);
    return sum + (negative ? -scaled : scaled);
  }, 0n);
  if (scale === 0) return total.toString();
  const negative = total < 0n;
  const digits = (negative ? -total : total).toString().padStart(scale + 1, "0");
  const whole = digits.slice(0, -scale);
  const fraction = digits.slice(-scale).replace(/0+$/, "");
  return `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}
