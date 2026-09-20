import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { createDemoMatrix, DemoGateway } from "../../src/shared/api/demo-gateway";
import type { ReportCellCoordinate } from "../../src/shared/api/report-cell-contract";
import { ReportMatrix } from "../../src/widgets/report-matrix/ReportMatrix";

afterEach(cleanup);

it.each(["entry", "admin"] as const)("shows one backend cumulative total and selected dates in %s mode", async workspaceMode => {
  const matrix = createDemoMatrix("DAILY_MOVEMENT");
  matrix.year = 2026;
  matrix.time_columns = ["2026-01-31", "2026-02-28", "2026-12-31"].map(id => ({
    id, label: id.slice(8), group_label: id.slice(0, 7), width: 64,
  }));
  matrix.rows = matrix.rows.slice(0, 3).map(row => ({ ...row, cells: row.cells.slice(0, 3).map((cell, index) => ({
    ...cell,
    column_id: matrix.time_columns[index]!.id,
    coordinate: { ...cell.coordinate, operation_date: matrix.time_columns[index]!.id } as ReportCellCoordinate,
    value: { kind: "QUANTITY" as const, quantity: "999" },
  })) }));
  matrix.daily_summary = {
    year: 2026, periods: Array.from({ length: 12 }, (_, index) => `2026-${String(index + 1).padStart(2, "0")}`),
    components: [],
    rows: matrix.rows.map((row, index) => ({
      row_id: row.id, annual: "1000", monthly: Array<string>(12).fill("111"),
      through_month: [index === 0 ? "0.1" : "", ...Array<string>(10).fill(index === 0 ? "0.3" : "0"), index === 0 ? "1000" : "8"],
    })),
  };
  render(<ReportMatrix workspaceMode={workspaceMode} matrix={matrix} gateway={new DemoGateway()} onChange={vi.fn()} onStatusChange={vi.fn()} />);
  const user = userEvent.setup();
  const table = screen.getByRole("table", { name: matrix.title });
  const totals = () => Array.from(table.querySelectorAll(".daily-summary-value"), cell => cell.textContent);
  expect(within(table).getAllByRole("columnheader", { name: "Накопительный итог" })).toHaveLength(1);
  expect(within(table).queryByRole("columnheader", { name: "С начала года" })).toBeNull();
  expect(within(table).queryByText("Сумма")).toBeNull();
  expect(within(table).getByRole("columnheader", { name: "Накопительный итог" })).toHaveAttribute("title", expect.stringContaining("по выбранный месяц включительно"));
  expect(totals()).toEqual(["0.1", "", ""]);
  const month = screen.getByRole("combobox", { name: "Месяц отчёта" });
  await user.selectOptions(month, "2026-02");
  expect(totals()).toEqual(["0.3", "0", "0"]);
  expect(within(table).queryByRole("columnheader", { name: /^31(?: |$)/ })).toBeNull();
  expect(within(table).getByRole("columnheader", { name: /^28(?: |$)/ })).toBeVisible();
  expect(within(table).queryByText("январь")).toBeNull();
  expect(screen.queryByText("Показать несколько месяцев")).toBeNull();
  expect(within(table).queryByRole("button", { name: /февраль/ })).toBeNull();
  await user.selectOptions(month, "2026-12");
  expect(totals()).toEqual(["1000", "8", "8"]);
  expect(within(table).queryByRole("columnheader", { name: /^28(?: |$)/ })).toBeNull();
  expect(within(table).getByRole("columnheader", { name: /^31(?: |$)/ })).toBeVisible();
  expect(table.querySelectorAll(".daily-summary-value")).toHaveLength(matrix.rows.length);
});
