import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { ApplicationGateway, ReportMatrixContract, SaveReportPresentationRequest } from "../../src/shared/api/application-gateway";
import type { ReportCellCoordinate } from "../../src/shared/api/report-cell-contract";
import { createDemoMatrix, DemoGateway } from "../../src/shared/api/demo-gateway";
import { ReportMatrix } from "../../src/widgets/report-matrix/ReportMatrix";

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
    return <ReportMatrix gateway={gateway} matrix={matrix} onChange={(next) => { latest = next; setMatrix(next); }} onStatusChange={vi.fn()} />;
  }
  render(<Workspace />);
  return () => latest;
}

it("expands months independently, skips hidden dates and keeps unsaved values", async () => {
  const latest = show(new DemoGateway());
  const user = userEvent.setup();
  const january = screen.getByRole("button", { name: "▾ январь", expanded: true });
  expect(january).toBeVisible();
  expect(screen.getAllByRole("cell")).toHaveLength(3);
  await user.dblClick(screen.getAllByRole("button", { name: /доступна для ввода/ })[0]!);
  await user.type(screen.getByRole("textbox"), "20{Enter}");
  await user.click(screen.getByRole("button", { name: "▸ февраль" }));
  expect(screen.getAllByRole("cell")).toHaveLength(6);
  await user.click(january);
  expect(screen.getAllByRole("cell")).toHaveLength(3);
  expect(latest().rows[0]!.cells[0]!.value).toEqual({ kind: "QUANTITY", quantity: "20" });
  await user.click(screen.getByRole("button", { name: "▸ январь" }));
  expect(screen.getByRole("button", { name: "значение 20, доступна для ввода" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Сохранить (1)" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "Свернуть все" }));
  expect(screen.queryAllByRole("cell")).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Раскрыть все" }));
  expect(screen.getAllByRole("cell")).toHaveLength(6);
});

it("renames the heading, saves resize gestures and removes the technical banner", async () => {
  const save = vi.fn(async (request: SaveReportPresentationRequest) => request);
  const gateway = Object.assign(new DemoGateway(), { saveReportPresentation: save });
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
