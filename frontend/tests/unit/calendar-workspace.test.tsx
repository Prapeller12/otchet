import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { ApplicationGateway, ReportMatrixContract, SaveReportPresentationRequest } from "../../src/shared/api/application-gateway";
import type { ReportCellCoordinate } from "../../src/shared/api/report-cell-contract";
import { createDemoMatrix, DemoGateway } from "../../src/shared/api/demo-gateway";
import { ReportMatrix } from "../../src/widgets/report-matrix/ReportMatrix";
import { sourceMatrix } from "../fixtures/source-matrix";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function calendar(): ReportMatrixContract {
  const matrix = createDemoMatrix("DAILY_MOVEMENT");
  matrix.year = 2026;
  matrix.time_columns = ["2026-01-14", "2026-02-01"].map((id) => ({ id, label: id.slice(8), group_label: id.slice(0, 7), width: 76 }));
  matrix.rows = matrix.rows.slice(0, 3).map((row, r) => ({ ...row, cells: row.cells.slice(0, 2).map((cell, c) => ({
    ...cell, column_id: matrix.time_columns[c]!.id,
    value: { kind: "DATA_NOT_PROVIDED" }, state: { access: r === 2 ? "calculated" : "editable", persistence: "saved" },
    coordinate: { ...cell.coordinate, operation_date: matrix.time_columns[c]!.id } as ReportCellCoordinate,
    ...(r === 2 ? { formula: "=BALANCE(RECEIVED,USED)" } : {}),
  })) }));
  return matrix;
}

function show(gateway: ApplicationGateway, initial = calendar()) {
  let latest = initial;
  function Workspace() {
    const [matrix, setMatrix] = useState(initial);
    return <ReportMatrix workspaceMode="admin" gateway={gateway} matrix={matrix} onChange={(next) => { latest = next; setMatrix(next); }} onStatusChange={vi.fn()} />;
  }
  render(<Workspace />);
  return () => latest;
}

it("shows only the selected daily month and keeps drafts while switching months", async () => {
  const latest = show(new DemoGateway());
  const user = userEvent.setup();
  const month = screen.getByRole("combobox", { name: "Месяц отчёта" });
  expect(month).toHaveValue("2026-01");
  expect(screen.queryByText("Показать несколько месяцев")).toBeNull();
  expect(screen.getAllByRole("cell")).toHaveLength(3);
  await user.dblClick(screen.getAllByRole("button", { name: /доступна для ввода/ })[0]!);
  await user.type(screen.getByRole("textbox"), "20{Enter}");
  await user.selectOptions(month, "2026-02");
  expect(screen.getAllByRole("cell")).toHaveLength(3);
  expect(screen.queryByRole("button", { name: "значение 20, доступна для ввода" })).toBeNull();
  expect(latest().rows[0]!.cells[0]!.value).toEqual({ kind: "QUANTITY", quantity: "20" });
  await user.selectOptions(month, "2026-01");
  expect(screen.getByRole("button", { name: "значение 20, доступна для ввода" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Сохранить (1)" })).toBeEnabled();
  expect(screen.getAllByRole("cell")).toHaveLength(3);
  expect(screen.queryByRole("button", { name: "Раскрыть все" })).toBeNull();
});

it("renames the heading, saves resize gestures and removes the technical banner", async () => {
  let stored = calendar();
  const save = vi.fn(async (request: SaveReportPresentationRequest) => {
    if (request.title) stored = { ...stored, title: request.title, matrix_revision: "renamed-revision" };
    return request;
  });
  const gateway = Object.assign(new DemoGateway(), { saveReportPresentation: save, getReportMatrix: vi.fn(async () => stored) });
  show(gateway);
  const user = userEvent.setup();
  expect(screen.queryByRole("note")).toBeNull();
  expect(screen.queryByText("Рабочая форма")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Переименовать отчёт" }));
  await user.clear(screen.getByLabelText("Название отчёта"));
  await user.type(screen.getByLabelText("Название отчёта"), "Сводка цеха");
  await user.click(screen.getByRole("button", { name: "Применить название" }));
  expect(await screen.findByRole("heading", { name: "Сводка цеха" })).toBeVisible();
  const handle = screen.getByRole("separator", { name: "Ширина: Изготовитель / объект" });
  fireEvent.keyDown(handle, { key: "ArrowRight" });
  await waitFor(() => expect(save).toHaveBeenLastCalledWith(expect.objectContaining({ widths: { subject: 260 } })));
  expect(handle).toHaveAttribute("aria-valuenow", "260");
  handle.setPointerCapture = vi.fn();
  vi.stubGlobal("PointerEvent", MouseEvent);
  fireEvent.pointerDown(handle, { clientX: 100 });
  fireEvent.pointerMove(handle, { clientX: 160 });
  fireEvent.pointerUp(handle, { clientX: 160 });
  await waitFor(() => expect(save).toHaveBeenLastCalledWith(expect.objectContaining({ widths: { subject: 320 } })));
  vi.unstubAllGlobals();
});

it("keeps legacy narrow numeric columns large enough and clamps keyboard resizing", async () => {
  const initial = calendar();
  initial.presentation = { widths: { "2026-01-14": 48 } };
  show(new DemoGateway(), initial);
  const handle = screen.getByRole("separator", { name: "Ширина: 2026-01 14" });
  expect(handle).toHaveAttribute("aria-valuenow", "64");
  fireEvent.keyDown(handle, { key: "Home" });
  expect(handle).toHaveAttribute("aria-valuenow", "64");
  fireEvent.keyDown(handle, { key: "ArrowLeft" });
  expect(handle).toHaveAttribute("aria-valuenow", "64");
});

it("requests backend draft calculation before save and retains both dirty inputs", async () => {
  const gateway = new DemoGateway();
  Object.defineProperty(gateway, "mode", { value: "pywebview" });
  const computed = calendar();
  computed.rows[2]!.cells[0]!.value = { kind: "QUANTITY", quantity: "17" };
  const preview = vi.spyOn(gateway, "getReportMatrix").mockResolvedValue(computed);
  const save = vi.spyOn(gateway, "saveReportCells");
  const latest = show(gateway);
  const user = userEvent.setup();
  const body = document.querySelector("tbody")!;
  for (const [row, quantity] of [[0, "20"], [1, "3"]] as const) {
    await user.dblClick(within(body.rows[row]!).getByRole("button", { name: /доступна для ввода/ }));
    await user.type(screen.getByRole("textbox"), `${quantity}{Enter}`);
    await waitFor(() => expect(screen.queryByText("Пересчёт…")).toBeNull());
  }
  expect(preview.mock.lastCall?.[0]).toMatchObject({ year: 2026, preview_changes: [
    { value: { kind: "QUANTITY", quantity: "20" } }, { value: { kind: "QUANTITY", quantity: "3" } },
  ] });
  expect(screen.getByRole("button", { name: "значение 17, расчётная ячейка" })).toBeVisible();
  expect(latest().rows[0]!.cells[0]!.state.persistence).toBe("dirty");
  expect(screen.getByRole("button", { name: "Сохранить (2)" })).toBeEnabled();
  expect(save).not.toHaveBeenCalled();
});

it("merges source totals and navigates between shared editable opening balances", async () => {
  const initial = sourceMatrix();
  show(new DemoGateway(), initial);
  const shared = document.querySelectorAll('td[data-shared="detail"]');
  // Main table: stock + variance per detail; separate input table: opening per detail.
  expect(shared).toHaveLength(6);
  expect(shared[0]).toHaveAttribute("rowspan", "2");
  const main = screen.getByRole("table", { name: initial.title });
  expect(main.querySelectorAll('td[data-shared="detail"]')).toHaveLength(4);
  expect(within(main).queryByRole("button", { name: /расчётная ячейка/ })).toBeNull();
  const auxiliary = screen.getByRole("table", { name: "Данные для расчёта" });
  expect(within(auxiliary).queryByRole("button", { name: "значение 999, доступна для ввода" })).toBeNull();
  const first = within(auxiliary).getByRole("button", { name: "значение 120, доступна для ввода" });
  const next = within(auxiliary).getByRole("button", { name: "значение 77, доступна для ввода" });
  fireEvent.keyDown(first, { key: "ArrowDown" });
  expect(next).toHaveFocus();
  fireEvent.keyDown(next, { key: "ArrowUp" });
  expect(first).toHaveFocus();
});
