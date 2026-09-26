import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { ApplicationGateway, ReportMatrixContract } from "../../src/shared/api/application-gateway";
import { createDemoMatrix, DemoGateway } from "../../src/shared/api/demo-gateway";
import { ReportMatrix } from "../../src/widgets/report-matrix/ReportMatrix";
import { sourceMatrix } from "../fixtures/source-matrix";

afterEach(cleanup);

function show(initial: ReportMatrixContract, gateway: ApplicationGateway) {
  function Workspace() {
    const [matrix, setMatrix] = useState(initial);
    return <ReportMatrix workspaceMode="entry" gateway={gateway} matrix={matrix} onChange={setMatrix} onStatusChange={() => {}} />;
  }
  return render(<Workspace />);
}

it("keeps configuration and extra tools out of the main entry screen and resizes only locally", async () => {
  const initial = sourceMatrix();
  initial.presentation!.header!.product_name = "Изделие А";
  initial.presentation!.header!.product_designation = "А-01";
  initial.presentation!.annual = { plan: "0", actual: "", completion: "", plan_months: 1, actual_months: 0 };
  initial.rows[0]!.workspace_id = "detail-a";
  initial.rows[0]!.supplier_id = "supplier-a";
  const save = vi.fn();
  const gateway = Object.assign(new DemoGateway(), { saveReportPresentation: save });
  const view = show(initial, gateway);
  const user = userEvent.setup();
  const header = screen.getByRole("region", { name: "Шапка изделия" });
  expect(within(header).getByText("Изделие А")).toBeVisible();
  expect(within(header).getByLabelText("Годовой план")).toHaveTextContent("0");
  expect(header.querySelectorAll("input")).toHaveLength(0);
  expect(screen.queryByRole("button", { name: "Переименовать отчёт" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Убрать производителя" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Импорт Excel" })).toBeNull();
  expect(within(view.container.querySelector(".matrix-toolbar") as HTMLElement).getAllByRole("button").map(button => button.textContent)).toEqual(["Сохранить", "Печать / PDF А4", "Ещё"]);
  fireEvent.keyDown(screen.getByRole("separator", { name: "Ширина: Наименование" }), { key: "ArrowRight" });
  expect(save).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Ещё" }));
  expect(screen.getByRole("button", { name: "Импорт Excel" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Экспорт Excel" })).toBeVisible();
});

it("saves the monthly fact through the main Save action, never includes the administrator plan", async () => {
  const initial = sourceMatrix();
  initial.presentation!.plans = { "2026-09": "500" };
  initial.presentation!.actuals = { "2026-09": "" };
  const save = vi.fn(async () => ({}));
  const gateway = Object.assign(new DemoGateway(), { saveReportPresentation: save, getReportMatrix: vi.fn(async () => initial) });
  show(initial, gateway);
  const user = userEvent.setup();
  expect(screen.getByLabelText("План выпуска, шт.")).toHaveAttribute("readonly");
  expect(screen.queryByRole("button", { name: "Сохранить план и выпуск" })).toBeNull();
  await user.type(screen.getByLabelText("Выпущено, шт."), "0");
  await user.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith({ report_type: initial.report_type, organization_id: initial.organization_id, expected_revision: initial.matrix_revision, confirmation: { year: 2026, month: 9 }, actuals: { "2026-09": "0" } }));
});

it("keeps plan rows visible while refusing entry edits and pastes into plans", async () => {
  const initial = createDemoMatrix("DAILY_MOVEMENT");
  initial.rows = initial.rows.slice(0, 1);
  initial.rows[0]!.cells.forEach(cell => {
    const { operation_type: _operation, metric_code: _metric, ...coordinate } = cell.coordinate;
    cell.coordinate = { ...coordinate, metric_code: "KIT_RELEASE_PLAN" };
    cell.state = { access: "editable", persistence: "saved" };
    cell.value = { kind: "QUANTITY", quantity: "0" };
  });
  const gateway = new DemoGateway();
  show(initial, gateway);
  const cell = screen.getAllByRole("button", { name: "подтверждённый ноль, заблокированная ячейка" })[0]!;
  const user = userEvent.setup();
  expect(cell).toHaveAttribute("data-field-hint", expect.stringContaining("План можно изменить в разделе «План и сведения»"));
  await user.dblClick(cell);
  expect(screen.queryByLabelText(/Редактирование:/)).toBeNull();
  fireEvent.paste(cell, { clipboardData: { getData: () => "222" } });
  expect(screen.queryByRole("dialog", { name: "Проверка вставки из Excel" })).toBeNull();
  expect(screen.getByRole("alert")).toHaveTextContent("заблокированную");
});

it("retains fact drafts when reviewer authorization is cancelled and allows retry", async () => {
  const initial = sourceMatrix();
  const save = vi.fn().mockRejectedValueOnce(new Error("Подтверждение отменено. Изменения не сохранены.")).mockResolvedValue({});
  const gateway = Object.assign(new DemoGateway(), { saveReportPresentation: save, getReportMatrix: vi.fn(async () => initial) });
  show(initial, gateway);
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Выпущено, шт."), "12");
  await user.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Подтверждение отменено");
  expect(screen.getByLabelText("Выпущено, шт.")).toHaveValue("12");
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
});

it("lets entry workers save code facts without sending administrator-owned labels or plans", async () => {
  const initial = sourceMatrix();
  initial.head_site = true;
  initial.report_type = "HEAD_SITE";
  initial.presentation!.production_codes = [
    { id: "A", label: "Код А", plans: { "2026-09": "5" }, actuals: { "2026-09": "1" } },
    { id: "B", label: "Код Б", plans: { "2026-09": "7" }, actuals: { "2026-09": "2" } },
  ];
  const save = vi.fn(async () => ({}));
  const gateway = Object.assign(new DemoGateway(), { saveReportPresentation: save, getReportMatrix: vi.fn(async () => initial) });
  show(initial, gateway);
  const user = userEvent.setup();
  await user.click(screen.getByText("Выпуск по кодам — 2"));
  const code = screen.getByLabelText("Код А: выпущено 2026-09");
  await user.clear(code);
  await user.type(code, "0");
  await user.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith({
    report_type: "HEAD_SITE", organization_id: initial.organization_id, expected_revision: initial.matrix_revision, confirmation: { year: 2026, month: 9 },
    production_code_actuals: { A: { "2026-09": "0" }, B: { "2026-09": "2" } }, confirm_production_totals: false,
  }));
});

it("prints next to Save using the selected period and blocks printing unsaved facts", async () => {
  const initial = sourceMatrix();
  const pdf = vi.fn(async () => ({ cancelled: true }));
  const gateway = Object.assign(new DemoGateway(), { exportPdf: pdf });
  const view = show(initial, gateway);
  const user = userEvent.setup();
  const toolbar = within(view.container.querySelector(".matrix-toolbar") as HTMLElement);
  const print = toolbar.getByRole("button", { name: "Печать / PDF А4" });
  expect(toolbar.getAllByRole("button").map(button => button.textContent)).toEqual(["Сохранить", "Печать / PDF А4", "Ещё"]);
  expect(screen.queryByRole("button", { name: "Подтвердить данные" })).toBeNull();
  await user.click(print);
  expect(pdf).toHaveBeenCalledWith(expect.objectContaining({ report_type: initial.report_type, organization_id: initial.organization_id, year: 2026, month: 9, expected_revision: initial.matrix_revision }));
  await user.type(screen.getByLabelText("Выпущено, шт."), "5");
  expect(print).toBeDisabled();
  expect(pdf).toHaveBeenCalledTimes(1);
});

it("lets responsible persons edit report metadata and plans without structural administration", async () => {
  const initial = sourceMatrix();
  initial.rows[0]!.workspace_id = "detail-a";
  initial.rows[0]!.supplier_id = "supplier-a";
  const save = vi.fn(async () => ({ ...initial.presentation, header: { ...initial.presentation!.header!, product_name: "Новое изделие" } }));
  const gateway = Object.assign(new DemoGateway(), { saveReportPresentation: save,
    getReportMatrix: vi.fn(async () => ({ ...initial, presentation: { ...initial.presentation, header: { ...initial.presentation!.header!, product_name: "Новое изделие" } } })) });
  function Workspace() {
    const [matrix, setMatrix] = useState(initial);
    return <ReportMatrix workspaceMode="report-settings" gateway={gateway} matrix={matrix} onChange={setMatrix} onStatusChange={() => {}} />;
  }
  render(<Workspace />);
  const user = userEvent.setup();
  expect(screen.getByLabelText("План выпуска, шт.")).not.toHaveAttribute("readonly");
  expect(screen.getByRole("button", { name: "Переименовать отчёт" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Убрать производителя" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Сохранить шапку и выпуск" })).toBeNull();
  await user.type(screen.getByLabelText("Наименование изделия"), "Новое изделие");
  await user.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ header: expect.objectContaining({ product_name: "Новое изделие" }), confirmation: { year: 2026, month: 9 } })));
  await waitFor(() => expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled());
});

it("clears a successfully saved fact draft but reports a failed signature explicitly", async () => {
  const initial = sourceMatrix();
  const save = vi.fn(async () => ({ actuals: { "2026-09": "12" }, verification_error: { code: "SIGNATURE_FAILED", message: "Проверьте расчётные значения" } }));
  const gateway = Object.assign(new DemoGateway(), { saveReportPresentation: save, getReportMatrix: vi.fn(async () => ({ ...initial, presentation: { ...initial.presentation, actuals: { "2026-09": "12" } } })) });
  show(initial, gateway);
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Выпущено, шт."), "12");
  await user.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Сохранено, но не подтверждено: Проверьте расчётные значения");
  await waitFor(() => expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled());
  expect(screen.getByLabelText("Выпущено, шт.")).toHaveValue("12");
});

it("reloads backend cumulative totals after saving even without formula rows", async () => {
  const initial = createDemoMatrix("DAILY_MOVEMENT");
  initial.year = 2026;
  initial.time_columns.forEach(column => { column.group_label = "2026-09"; });
  initial.daily_summary = { year: 2026, periods: [], rows: initial.rows.map(row => ({ row_id: row.id, annual: "", monthly: [], through_month: Array(12).fill("7") })), components: [] };
  initial.rows.forEach(row => row.cells.forEach(cell => { delete cell.formula; }));
  const updated = { ...initial, daily_summary: { ...initial.daily_summary, rows: initial.daily_summary.rows.map(row => ({ ...row, through_month: Array(12).fill("23") })) } };
  const read = vi.fn(async () => updated);
  const gateway = Object.assign(new DemoGateway(), { getReportMatrix: read, saveReportCells: vi.fn(async () => ({ matrix_revision: "r2", cells: [] })) });
  show(initial, gateway);
  const user = userEvent.setup();
  const first = screen.getAllByRole("button", { name: /доступна для ввода/ })[0]!;
  await user.dblClick(first);
  await user.clear(screen.getByRole("textbox"));
  await user.type(screen.getByRole("textbox"), "16{Enter}");
  await user.click(screen.getByRole("button", { name: "Сохранить (1)" }));
  await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  expect(document.querySelector(".daily-summary-value")).toHaveTextContent("23");
});
