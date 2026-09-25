import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { HintValue } from "../../src/shared/ui/FieldHint";
import { reportCellHint, REPORT_FIELD_HINTS } from "../../src/shared/config/report-field-hints";
import { ReportCellView } from "../../src/widgets/report-matrix/ReportCellView";
import { ReportMatrix } from "../../src/widgets/report-matrix/ReportMatrix";
import { ProductionHeader } from "../../src/widgets/report-matrix/ProductionHeader";
import { ReferenceGrid } from "../../src/features/reference-reports/ReferenceReport";
import { createDemoMatrix, DemoGateway } from "../../src/shared/api/demo-gateway";
import type { MatrixCellContract, ReportMatrixContract } from "../../src/shared/api/application-gateway";
import { sourceMatrix } from "../fixtures/source-matrix";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("shows keyboard help outside overflow, clamps it at the bottom/right edge and dismisses on Escape or scrolling", () => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    return this.classList.contains("field-hint-popup")
      ? new DOMRect(0, 0, 430, 200) : new DOMRect(950, 700, 40, 40);
  });
  const { container } = render(<div style={{ overflow: "hidden" }}><HintValue hint="Заполните остаток в данных для расчёта">—</HintValue></div>);
  const value = screen.getByText("—");
  fireEvent.focus(value);
  const tip = screen.getByRole("tooltip");
  expect(tip.parentElement).toBe(document.body);
  expect(container.contains(tip)).toBe(false);
  expect(value).toHaveAttribute("aria-describedby", tip.id);
  expect(value).toHaveAccessibleDescription("Заполните остаток в данных для расчёта");
  expect(parseFloat(tip.style.left) + 430).toBeLessThanOrEqual(window.innerWidth - 8);
  expect(parseFloat(tip.style.top) + 200).toBeLessThanOrEqual(window.innerHeight - 8);
  expect(parseFloat(tip.style.top)).toBeGreaterThanOrEqual(8);
  fireEvent.keyDown(value, { key: "Escape" });
  expect(screen.queryByRole("tooltip")).toBeNull();
  fireEvent.mouseEnter(value);
  expect(screen.getByRole("tooltip")).toBeVisible();
  fireEvent.scroll(container.firstChild!);
  expect(screen.queryByRole("tooltip")).toBeNull();
});

it("keeps only one tooltip when focus stays on A and the pointer moves to B, and lets the pointer reach the help", () => {
  vi.useFakeTimers();
  render(<><HintValue hint="Подсказка А">А</HintValue><HintValue hint="Подсказка Б">Б</HintValue></>);
  fireEvent.focus(screen.getByText("А"));
  fireEvent.mouseEnter(screen.getByText("Б"));
  expect(screen.getAllByRole("tooltip")).toHaveLength(1);
  expect(screen.getByRole("tooltip")).toHaveTextContent("Подсказка Б");
  expect(screen.getByText("А")).not.toHaveAttribute("aria-describedby");
  fireEvent.mouseLeave(screen.getByText("Б"));
  fireEvent.mouseEnter(screen.getByRole("tooltip"));
  act(() => vi.advanceTimersByTime(200));
  expect(screen.getByRole("tooltip")).toBeVisible();
  fireEvent.mouseLeave(screen.getByRole("tooltip"));
  act(() => vi.advanceTimersByTime(200));
  expect(screen.queryByRole("tooltip")).toBeNull();
});

it("preserves the grid's roving focus and edit guard while calculated zero gets the same useful help as missing data", () => {
  const base = sourceMatrix().rows[0]!.cells.find(cell => cell.state.access === "calculated")!;
  base.coordinate = { ...base.coordinate, metric_code: "SUB_STOCK_TEST" } as MatrixCellContract["coordinate"];
  const key = vi.fn(); const edit = vi.fn(); const activate = vi.fn();
  const props = { position: { row: 0, column: 2 }, active: false, onActivate: activate, onEdit: edit, onKeyDown: key };
  const { rerender } = render(<ReportCellView {...props} cell={{ ...base, value: { kind: "QUANTITY", quantity: "0" } }} />);
  const zero = screen.getByRole("button", { name: /подтверждённый ноль/ });
  expect(zero).toHaveAttribute("tabindex", "-1");
  fireEvent.focus(zero);
  expect(screen.getByRole("tooltip")).toHaveTextContent("Данные для расчёта");
  fireEvent.keyDown(zero, { key: "ArrowRight" });
  expect(key).toHaveBeenCalledWith(expect.anything(), props.position);
  fireEvent.doubleClick(zero);
  expect(edit).not.toHaveBeenCalled();
  rerender(<ReportCellView {...props} cell={{ ...base, value: { kind: "DATA_NOT_PROVIDED" }, issue: { code: "MISSING_INPUT", message: "Не указан остаток позиции А" } }} />);
  expect(screen.getByRole("button")).toHaveTextContent("Заполните");
  expect(screen.getByRole("tooltip")).toHaveTextContent("Не указан остаток позиции А");
  expect(screen.getByRole("tooltip")).toHaveTextContent("Введите 0 только если подтверждено");
});

it.each(["HEAD_SITE", "SUBSIDIARY"] as const)("explains %s stock, deficit, metadata and readonly plan without modifying source values", reportType => {
  const matrix = sourceMatrix();
  matrix.report_type = reportType; matrix.head_site = reportType === "HEAD_SITE";
  matrix.rows.forEach(row => row.cells.forEach(cell => { cell.coordinate.report_type = reportType; }));
  if (matrix.head_site) {
    const stock = matrix.rows[0]!.cells[2]!;
    const hint = reportCellHint(stock, matrix, matrix.rows[0])!;
    expect(hint).toContain("Выпущено готовых изделий");
  }
  const before = JSON.stringify(matrix);
  const change = vi.fn();
  render(<ReportMatrix matrix={matrix} workspaceMode="entry" gateway={new DemoGateway()} onChange={change} onStatusChange={vi.fn()} />);
  const table = screen.getByRole("table", { name: matrix.title });
  const stock = table.querySelector<HTMLElement>(".source-calculated .source-readonly-value")!;
  fireEvent.mouseEnter(stock);
  expect(screen.getByRole("tooltip")).toHaveTextContent("Данные для расчёта");
  expect(screen.getByRole("tooltip")).toHaveTextContent("Остаток на начало");
  expect(stock).toHaveAttribute("tabindex", "0");
  for (const metadata of table.querySelectorAll("tbody th.source-identity-cell")) {
    const anchor = metadata.querySelector("[data-field-hint]");
    expect(anchor).not.toBeNull();
    expect(anchor).toHaveAttribute("data-field-hint", expect.stringContaining("Настроить рабочее поле"));
  }
  const plan = screen.getByRole("textbox", { name: matrix.head_site ? "План готовых изделий, шт." : "План выпуска, шт." });
  fireEvent.focus(plan);
  expect(screen.getByRole("tooltip")).toHaveTextContent("План и сведения");
  expect(plan).toHaveAttribute("readonly");
  const auxiliary = screen.getByRole("table", { name: "Данные для расчёта" });
  for (const field of auxiliary.querySelectorAll('button[aria-readonly="true"]')) expect(field).toHaveAttribute("data-field-hint");
  expect(change).not.toHaveBeenCalled();
  expect(JSON.stringify(matrix)).toBe(before);
});

it("resolves the labels of custom daily formula inputs without calculating their values in the frontend", () => {
  const matrix = createDemoMatrix("DAILY_MOVEMENT");
  matrix.rows = matrix.rows.slice(0, 2);
  const input = matrix.rows[0]!; const total = matrix.rows[1]!;
  total.group_id = input.group_id;
  input.left_values.indicator = "Поступило деталей";
  input.cells[0]!.coordinate = { ...input.cells[0]!.coordinate, metric_code: "INPUT_A" } as MatrixCellContract["coordinate"];
  total.cells[0] = { ...total.cells[0]!, coordinate: { ...total.cells[0]!.coordinate, metric_code: "CUSTOM" } as MatrixCellContract["coordinate"], formula: "=OPENING+CUM(INPUT_A)", state: { access: "calculated", persistence: "saved" } };
  const hint = reportCellHint(total.cells[0], matrix, total)!;
  expect(hint).toContain("«Поступило деталей»");
  expect(hint).toContain("Начальный остаток, дату его начала");
  expect(hint).toContain("Формула: =OPENING+CUM(INPUT_A)");
  expect(REPORT_FIELD_HINTS.cumulative).toContain("Для расчётных строк, включая остаток и готовые комплекты, — значение на конец выбранного месяца");
});

it("gives missing daily calculation, cumulative and monthly summary fields keyboard-accessible help", () => {
  const matrix: ReportMatrixContract = createDemoMatrix("DAILY_MOVEMENT");
  matrix.year = 2026;
  matrix.daily_summary = { year: 2026, periods: Array.from({ length: 12 }, (_, i) => `2026-${String(i + 1).padStart(2, "0")}`),
    rows: matrix.rows.map(row => ({ row_id: row.id, annual: "", monthly: Array(12).fill(""), through_month: Array(12).fill("") })),
    components: [{ group_id: "component", party: "Поставщик", position: "Деталь", rows: ["RECEIVED", "USED", "BALANCE"].map(metric_code => ({ row_id: metric_code, metric_code, annual: "0", monthly: Array(12).fill(""), through_month: Array(12).fill("") })) }] };
  render(<ReportMatrix matrix={matrix} workspaceMode="entry" gateway={new DemoGateway()} onChange={vi.fn()} onStatusChange={vi.fn()} />);
  const table = screen.getByRole("table", { name: matrix.title });
  for (const cell of table.querySelectorAll('button[aria-readonly="true"]')) expect(cell).toHaveAttribute("data-field-hint");
  const cumulative = table.querySelector<HTMLElement>(".daily-summary-value [data-field-hint]")!;
  expect(cumulative.textContent).toBe("");
  fireEvent.focus(cumulative);
  expect(screen.getByRole("tooltip")).toHaveTextContent("Пустое значение не равно нулю");
  const summary = screen.getByRole("region", { name: "Месячная сводка составных частей" });
  for (const cell of within(summary).getAllByRole("cell")) expect(cell.querySelector('[tabindex="0"][data-field-hint]')).not.toBeNull();
});

it("explains static product header outputs even before data has been entered", () => {
  render(<ProductionHeader workspaceMode="entry" presentation={{}} year={2026} headSite blocked={false} onSave={vi.fn()} onDirtyChange={vi.fn()} />);
  fireEvent.focus(screen.getByText("Не задано").closest("[data-field-hint]")!);
  expect(screen.getByRole("tooltip")).toHaveTextContent("План и сведения");
  expect(screen.getByRole("tooltip")).toHaveTextContent("Наименование изделия");
  const plan = screen.getByLabelText("Годовой план").closest("[data-field-hint]")!;
  fireEvent.mouseEnter(plan);
  expect(screen.getAllByRole("tooltip")).toHaveLength(1);
  expect(screen.getByRole("tooltip")).toHaveTextContent("сохранённых месячных планов");
});

it("identifies readonly Excel fields and formula dependencies while preserving the existing input editor", () => {
  const edit = vi.fn();
  render(<ReferenceGrid onEdit={edit} sheet={{ name: "Исходный лист", rows: 9, columns: 2, merges: [], cells: {
    A1: { kind: "s", value: "Заголовок", display: "Заголовок" },
    A9: { kind: "n", value: "0", display: "0" }, B9: { kind: "f", value: "=A9*2", display: "0" },
  } }} />);
  fireEvent.mouseEnter(screen.getByText("Заголовок"));
  expect(screen.getByRole("tooltip")).toHaveTextContent("исходной книги Excel, ячейка A1");
  const formula = document.querySelector<HTMLElement>(".reference-formula [data-field-hint]")!;
  fireEvent.focus(formula);
  expect(screen.getByRole("tooltip")).toHaveTextContent("=A9*2");
  expect(screen.getByRole("tooltip")).toHaveTextContent("Сохранить изменения");
  fireEvent.doubleClick(document.querySelector(".reference-editable")!);
  expect(screen.getByRole("textbox", { name: "Значение A9" })).toBeVisible();
  expect(edit).not.toHaveBeenCalled();
});
