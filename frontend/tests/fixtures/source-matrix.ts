import type { ReportMatrixContract } from "../../src/shared/api/application-gateway";
import { createDemoMatrix } from "../../src/shared/api/demo-gateway";

/** Synthetic current source shape: two suppliers share one detail, third another. */
export function sourceMatrix(): ReportMatrixContract {
  const matrix = createDemoMatrix("SUBSIDIARY");
  matrix.year = 2026;
  matrix.subsidiary = true;
  matrix.presentation = { plans: {}, actuals: {}, header: { product_designation: "", product_name: "", factory_name: "", product_image: "" } };
  matrix.time_columns = ["OPENING", "RECEIVED", "STOCK", "VARIANCE", "USED"].map(kind => ({
    id: kind === "USED" ? "2026-09-01" : `2026-09-${kind}`,
    kind, label: kind === "USED" ? "01–06" : kind, group_label: "2026-09", width: 110,
  }));
  matrix.rows = [120, 999, 77].map((quantity, rowIndex) => ({
    id: `supplier-${rowIndex}`, group_id: rowIndex < 2 ? "detail-a" : "detail-b", group_label: rowIndex < 2 ? "Деталь А" : "Деталь Б",
    left_values: { number: rowIndex < 2 ? "1" : "2", designation: "ABC", position: rowIndex < 2 ? "Деталь А" : "Деталь Б", norm: "2", party: `Завод ${rowIndex + 1}`, contract: "1000" },
    cells: matrix.time_columns.map(column => ({
      column_id: column.id,
      coordinate: { report_type: "SUBSIDIARY", organization_id: matrix.organization_id, component_id: rowIndex < 2 ? "detail-a" : "detail-b", metric_code: `${column.kind}_${["OPENING", "STOCK", "VARIANCE"].includes(column.kind!) ? "SHARED" : rowIndex}`, period_start: "2026-09-01" },
      value: { kind: "QUANTITY", quantity: String(quantity) },
      state: { access: ["STOCK", "VARIANCE"].includes(column.kind!) ? "calculated" : "editable", persistence: "saved" },
    })),
  }));
  return matrix;
}
