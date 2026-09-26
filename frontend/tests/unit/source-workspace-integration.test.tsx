import { useState } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DemoGateway } from "../../src/shared/api/demo-gateway";
import { ReportMatrix } from "../../src/widgets/report-matrix/ReportMatrix";
import { sourceMatrix } from "../fixtures/source-matrix";

afterEach(cleanup);

function show(initial = sourceMatrix()) {
  const gateway = Object.assign(new DemoGateway(), { saveReportPresentation: vi.fn(async () => ({})) });
  let latest = initial;
  const save = vi.spyOn(gateway, "saveReportCells").mockImplementation(async request => ({
    matrix_revision: "saved-2", cells: request.changes.map(change => ({ ...change, state: { access: "editable" as const, persistence: "saved" as const } })),
  }));
  function Workspace() {
    const [matrix, setMatrix] = useState(initial);
    return <ReportMatrix workspaceMode="admin" gateway={gateway} matrix={matrix} onChange={next => { latest = next; setMatrix(next); }} onStatusChange={() => {}} />;
  }
  render(<Workspace />);
  return { initial, save, latest: () => latest };
}

it("source projection edits supplier receipts at the original coordinate, without touching shared opening", async () => {
  const { initial, latest, save } = show();
  const main = screen.getByRole("table", { name: initial.title });
  const firstRow = main.querySelector("tbody tr")!;
  const receipt = within(firstRow as HTMLElement).getAllByRole("button", { name: /доступна для ввода/ })[0]!;
  const user = userEvent.setup();
  await user.dblClick(receipt);
  const editor = screen.getByLabelText("Редактирование: ячейка");
  await user.clear(editor);
  await user.type(editor, "321{Enter}");
  expect(latest().rows[0]!.cells[1]!.value).toEqual({ kind: "QUANTITY", quantity: "321" });
  expect(latest().rows[0]!.cells[0]!.value).toEqual({ kind: "QUANTITY", quantity: "120" });
  expect(screen.getByRole("button", { name: "Переименовать отчёт" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Сохранить (1)" }));
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ changes: [{ coordinate: initial.rows[0]!.cells[1]!.coordinate, value: { kind: "QUANTITY", quantity: "321" } }] }));
});

it("header drafts block source editing and paste until explicitly cancelled", async () => {
  const { initial, latest, save } = show();
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Шифр изделия"), "NEW");
  const cell = within(screen.getByRole("table", { name: initial.title })).getAllByRole("button", { name: /доступна для ввода/ })[0]!;
  await user.dblClick(cell);
  expect(screen.queryByLabelText("Редактирование: ячейка")).toBeNull();
  fireEvent.paste(cell, { clipboardData: { getData: () => "222" } });
  expect(screen.queryByRole("dialog", { name: "Проверка вставки из Excel" })).toBeNull();
  expect(screen.getByRole("button", { name: "Переименовать отчёт" })).toBeDisabled();
  expect(latest().rows[0]!.cells[1]!.value).toEqual(initial.rows[0]!.cells[1]!.value);
  expect(save).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Отменить изменения шапки" }));
  await user.dblClick(cell);
  expect(screen.getByLabelText("Редактирование: ячейка")).toBeVisible();
});


it("shows weekly receipts separately from consumption, edits their coordinate and preserves monthly calculated receipts", async () => {
  const initial = sourceMatrix();
  const column = { id: "2026-09-01-SUPPLIED", kind: "SUPPLIED", label: "Поставлено 01–06", group_label: "2026-09", width: 100 };
  initial.time_columns.splice(4, 0, column);
  initial.rows.forEach((row, index) => {
    row.cells[1]!.state.access = "calculated";
    row.cells.splice(4, 0, { ...row.cells[1]!, column_id: column.id, coordinate: { report_type: "SUBSIDIARY", organization_id: initial.organization_id, component_id: index < 2 ? "detail-a" : "detail-b", metric_code: `SUB_SUPPLIED_${index}`, period_start: "2026-09-01" }, value: { kind: "QUANTITY", quantity: String(41 + index) }, state: { access: "editable", persistence: "saved" } });
  });
  const { latest, save } = show(initial);
  expect(screen.getByRole("button", { name: "Поступило 01–06" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Расход 01–06" })).toBeVisible();
  const user = userEvent.setup();
  const supplied = screen.getByRole("button", { name: "значение 41, доступна для ввода" });
  await user.click(supplied);
  await user.tab();
  expect(document.activeElement).toHaveAttribute("aria-label", "значение 120, доступна для ввода");
  await user.dblClick(supplied);
  await user.clear(screen.getByLabelText("Редактирование: ячейка"));
  await user.type(screen.getByLabelText("Редактирование: ячейка"), "55{Enter}");
  expect(latest().rows[0]!.cells[4]!.value).toEqual({ kind: "QUANTITY", quantity: "55" });
  expect(latest().rows[0]!.cells[1]!.state.access).toBe("calculated");
  await user.click(screen.getByRole("button", { name: "Сохранить (1)" }));
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ changes: [{ coordinate: initial.rows[0]!.cells[4]!.coordinate, value: { kind: "QUANTITY", quantity: "55" } }] }));
});
