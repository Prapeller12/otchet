import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DemoGateway } from "../../src/shared/api/demo-gateway";
import { ReportMatrix } from "../../src/widgets/report-matrix/ReportMatrix";
import { sourceMatrix } from "../fixtures/source-matrix";

afterEach(cleanup);

it.each([false, true])("opens the primary month for a readonly first supplier (head=%s)", head => {
  const matrix = sourceMatrix();
  matrix.head_site = head;
  if (head) {
    matrix.report_type = "HEAD_SITE";
    matrix.title = "Головная площадка";
    matrix.time_columns[1]!.kind = "FACT";
  }
  matrix.rows[0]!.cells.forEach(cell => { cell.state.access = "locked"; });
  render(<ReportMatrix matrix={matrix} gateway={new DemoGateway()} onChange={vi.fn()} onStatusChange={vi.fn()} />);
  const primary = screen.getByRole("table", { name: matrix.title });
  const active = primary.querySelector('.matrix-cell-button[tabindex="0"]');
  expect(active).toHaveAttribute("data-row", "0");
  expect(active).toHaveAttribute("data-column", "1");
  expect(active).toHaveFocus();
  const scrollRegion = screen.getByRole("region", { name: "Таблица отчёта" });
  expect(scrollRegion).toContainElement(primary);
  expect(scrollRegion).toContainElement(screen.getByRole("table", { name: "Данные для расчёта" }));
  expect(within(primary).getAllByRole("row").slice(2)).toHaveLength(matrix.rows.length);
  expect([...primary.querySelectorAll("thead [data-identity-column]")].map(column => column.getAttribute("data-identity-column")))
    .toEqual(["photo", "number", "designation", "position", "norm", "party"]);
});
