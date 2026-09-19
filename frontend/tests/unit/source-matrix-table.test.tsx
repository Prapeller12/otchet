import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SourceMatrixTable } from "../../src/widgets/report-matrix/SourceMatrixTable";
import { createDemoMatrix } from "../../src/shared/api/demo-gateway";
import type { MatrixRowContract, ReportMatrixContract } from "../../src/shared/api/application-gateway";

afterEach(cleanup);

function source(head: boolean): ReportMatrixContract {
  const matrix = createDemoMatrix(head ? "HEAD_SITE" : "SUBSIDIARY");
  matrix.subsidiary = true;
  matrix.head_site = head;
  const kinds = head ? ["OPENING", "PLAN", "FACT", "USED", "STOCK", "VARIANCE"] : ["OPENING", "RECEIVED", "STOCK", "VARIANCE", "USED"];
  matrix.time_columns = ["2026-01", "2026-02"].flatMap(month => kinds.map(kind => ({ id: kind === "USED" && !head ? month + "-02" : `${month}-${kind}`, label: kind === "USED" && !head ? "02–08" : kind === "PLAN" ? "План" : kind === "FACT" ? "Факт" : kind, kind, group_label: month, width: 100 })));
  const cell = matrix.rows[0]!.cells[0]!;
  matrix.rows = ["Завод А", "Завод Б"].map((party, index): MatrixRowContract & { manufactured_total: string } => ({
    id: `row-${index}`, group_id: "detail-1", group_label: "Деталь", image: "data:image/png;base64,AA==", manufactured_total: index ? "45" : "123",
    left_values: { number: "1", designation: "A-1", position: "Деталь", norm: "2", party, contract: index ? "50" : "100" },
    stock_by_week: { "2026-02-02": { kind: "QUANTITY", quantity: "88" } },
    cells: matrix.time_columns.map((column, columnIndex) => ({ ...cell, column_id: column.id, value: { kind: "QUANTITY", quantity: String(columnIndex + 10 * index) } })),
  }));
  return matrix;
}

function show(head: boolean) {
  const matrix = source(head);
  const cells = vi.fn((row: number, column: number) => <td key={column} data-coordinate={`${row}:${column}`}>editable-{row}-{column}</td>);
  const week = vi.fn();
  const paste = vi.fn();
  render(<SourceMatrixTable matrix={matrix} summaryMonth="2026-02" stockWeek="2026-02-02" visibleIndices={matrix.time_columns.map((_, index) => index)} renderValueCell={cells} onSelectStockWeek={week} onPaste={paste} />);
  return { matrix, cells, week, paste };
}

it("keeps subsidiary reference column order, shared photo/stock and supplier-specific receipts", () => {
  const { matrix, cells, week, paste } = show(false);
  const table = screen.getByRole("table", { name: matrix.title });
  const headers = within(table).getAllByRole("columnheader").map(node => node.textContent);
  expect(headers.slice(0, 10)).toEqual(["Условное изображение", "№ п/п", "Обозначение", "Наименование", "Входимость в изделие (шт.)", "Производитель", "В наличии на складе", "Объём поставок по договору", "Поступило за месяц", "Общий профицит / дефицит ДСЕ"]);
  expect(within(table).getAllByRole("img")).toHaveLength(1);
  expect(within(table).getByText("88").closest("td")).toHaveAttribute("rowspan", "2");
  expect(within(table).getByText("100")).toBeInTheDocument();
  expect(within(table).getByText("50")).toBeInTheDocument();
  // Only February receipts and weekly consumption are interactive in the main table.
  expect(table.querySelector('[data-coordinate="0:6"]')).not.toBeNull();
  expect(table.querySelector('[data-coordinate="1:6"]')).not.toBeNull();
  expect(table.querySelector('[data-coordinate="0:4"]')).not.toBeNull();
  expect(table.querySelector('[data-coordinate="0:9"]')).not.toBeNull();
  expect(table.querySelector('[data-coordinate="0:5"]')).toBeNull();
  const auxiliary = screen.getByRole("table", { name: "Данные для расчёта" });
  expect(auxiliary.querySelector('[data-coordinate="0:5"]')).not.toBeNull();
  expect(auxiliary.querySelector('[data-coordinate="1:5"]')).toBeNull();
  expect(cells).toHaveBeenCalledTimes(7);
  const weekButtons = within(table).getAllByRole("button", { name: /Расход/ });
  fireEvent.click(weekButtons[1]!);
  expect(week).toHaveBeenCalledWith("2026-02-02");
  fireEvent.paste(table);
  expect(paste).toHaveBeenCalledTimes(1);
});

it("keeps head reference summary columns and month plan/fact pairs without duplicating summary editors", () => {
  const { matrix } = show(true);
  const table = screen.getByRole("table", { name: matrix.title });
  const headers = within(table).getAllByRole("columnheader").map(node => node.textContent);
  expect(headers.slice(5, 9)).toEqual(["Контрагент", "В наличии на складе", "Всего изготовлено за год", "Изготовлено в текущем месяце"]);
  expect(headers.slice(-4)).toEqual(["План", "Факт", "План", "Факт"]);
  expect(within(table).getByText("123")).toBeInTheDocument();
  expect(within(table).getByText("45")).toBeInTheDocument();
  expect(within(table).getByText("8")).toBeInTheDocument(); // February FACT mirror, readonly.
  expect(table.querySelectorAll('[data-coordinate="0:8"]')).toHaveLength(1);
  expect(table.querySelector('[data-coordinate="0:6"]')).toBeNull();
  const auxiliary = screen.getByRole("table", { name: "Данные для расчёта" });
  expect(auxiliary.querySelector('[data-coordinate="0:6"]')).not.toBeNull();
  expect(auxiliary.querySelector('[data-coordinate="0:9"]')).not.toBeNull();
  expect(auxiliary.querySelector('[data-coordinate="0:11"]')).not.toBeNull();
  expect(within(auxiliary).getByText("100")).toBeInTheDocument();
  expect(within(auxiliary).getByText("50")).toBeInTheDocument();
});
