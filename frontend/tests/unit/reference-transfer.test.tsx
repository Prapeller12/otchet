import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ReferenceTransfer } from "../../src/features/reference-reports/ReferenceTransfer";
import { createDemoMatrix, DemoGateway } from "../../src/shared/api/demo-gateway";
import type { ImportPreview, ReferenceWorkbook, TransferSource } from "../../src/shared/api/application-gateway";

afterEach(cleanup);
const book: ReferenceWorkbook = { id: "book", file_name: "head.xlsx", report_type: "HEAD_SITE", revision: 0, warnings: [], errors: [], sheets: [
  { name: "Лист", rows: 100, columns: 109, merges: [], cells: { C4: { kind: "n", value: "0.45", display: "45%" }, DE99: { kind: "n", value: "375", display: "375" } } },
] };
const source: TransferSource = { sheet: "Лист", address: "DE99", value: "375", role: "INPUT", unit: "шт" };
function setup(initial: ImportPreview, second: ImportPreview = { cancelled: false, batch_id: "checked", status: "STAGED", error_count: 0 }) {
  const matrix = createDemoMatrix("HEAD_SITE");
  const referenceReport = vi.fn().mockResolvedValueOnce(initial).mockResolvedValue(second);
  const gateway = Object.assign(new DemoGateway(), { referenceReport }); const onReady = vi.fn();
  render(<ReferenceTransfer book={book} matrix={matrix} gateway={gateway} onReady={onReady} />);
  return { matrix, referenceReport, onReady, user: userEvent.setup() };
}
it("uses server-classified sources without scanning percentages or limiting worksheet bounds", async () => {
  const { referenceReport, onReady } = setup({ cancelled: false, sources: { "0:DE99": source }, error_count: 1,
    issues: [{ source_cell: "0:DE99", code: "MAPPING_REQUIRED", message: "Выберите позицию и показатель" }] });
  expect(await screen.findByLabelText("Показатель 0:DE99")).toHaveAttribute("aria-invalid", "true");
  expect(screen.queryByLabelText("Показатель 0:C4")).toBeNull();
  expect(referenceReport).toHaveBeenCalledWith(expect.objectContaining({ mode: "append", mappings: [] }));
  expect(onReady).toHaveBeenLastCalledWith(expect.objectContaining({ error_count: 1, reference_workbook: book }));
});
it("sends only explicit overrides and clears the stale issue before rechecking the whole package", async () => {
  const { matrix, referenceReport, onReady, user } = setup({ cancelled: false, sources: { "0:DE99": source }, error_count: 1,
    issues: [{ source_cell: "0:DE99", code: "MAPPING_REQUIRED", message: "Выберите позицию и показатель" }] });
  const row = matrix.rows.find(row => row.cells[0]?.state.access === "editable")!;
  await user.selectOptions(await screen.findByLabelText("Показатель 0:DE99"), row.id);
  expect(onReady).toHaveBeenLastCalledWith(expect.objectContaining({ validation_pending: true, issues: [] }));
  await user.selectOptions(screen.getByLabelText("Период 0:DE99"), row.cells[0]!.column_id);
  await user.clear(screen.getByLabelText("Значение 0:DE99")); await user.type(screen.getByLabelText("Значение 0:DE99"), "0");
  await user.click(screen.getByRole("button", { name: "Проверить сопоставление" }));
  expect(referenceReport).toHaveBeenLastCalledWith(expect.objectContaining({ mappings: [{ source: "0:DE99", coordinate: row.cells[0]!.coordinate, quantity: "0", confirmed: true }] }));
  await waitFor(() => expect(onReady).toHaveBeenLastCalledWith(expect.objectContaining({ batch_id: "checked", error_count: 0, validation_pending: false })));
});
it("automatically checks known mappings and revalidates when the import mode changes", async () => {
  const { user, referenceReport } = setup({ cancelled: false, batch_id: "auto", status: "STAGED", error_count: 0, sources: { "0:DE99": source } });
  await screen.findByLabelText("Показатель 0:DE99");
  expect(screen.queryByLabelText("Значение и период верны")).toBeNull();
  await user.selectOptions(screen.getByLabelText("Режим переноса"), "update");
  expect(referenceReport).toHaveBeenLastCalledWith(expect.objectContaining({ mode: "update", mappings: [] }));
});
it("sends one explicitly dated rule for the whole source column", async () => {
  const { user, referenceReport } = setup({ cancelled: false, sources: { "0:DE99": source }, recognition: { period_blocks: [
    { key: "0:DE", sheet: "Лист", address: "DE7", label: "1 неделя", year: 2026, month: 9 },
  ] } });
  await user.type(await screen.findByLabelText("Начало периода 0:DE"), "2026-09-01");
  await user.type(screen.getByLabelText("Конец периода 0:DE"), "2026-09-06");
  await user.type(screen.getByLabelText("Основание периода 0:DE"), "Календарь исходного отчёта");
  await user.click(screen.getByRole("button", { name: "Проверить сопоставление" }));
  expect(referenceReport).toHaveBeenLastCalledWith(expect.objectContaining({ period_rules: { "0:DE": { start: "2026-09-01", end: "2026-09-06", calendar: "USER_CONFIRMED", reason: "Календарь исходного отчёта" } } }));
});

it("requires an explicit decision and reason for an unknown hidden sheet", async () => {
  const { user, referenceReport } = setup({ cancelled: false, sources: {}, recognition: {
    sheets: [{ index: 2, name: "Архив", state: "hidden", decision_required: true }],
  } });
  await user.selectOptions(await screen.findByLabelText("Действие для листа Архив"), "exclude");
  await user.type(screen.getByLabelText("Причина исключения листа Архив"), "Архив другого периода");
  await user.click(screen.getByRole("button", { name: "Проверить сопоставление" }));
  expect(referenceReport).toHaveBeenLastCalledWith(expect.objectContaining({ sheet_decisions: { "2": { include: false, reason: "Архив другого периода" } } }));
});

it("shows new projected positions as resolved and reuses saved period and sheet decisions", async () => {
  const target = createDemoMatrix("HEAD_SITE");
  target.rows = target.rows.filter(row => row.cells[0]?.state.access === "editable").slice(0, 1);
  target.rows[0]!.id = "new-projected-position";
  target.rows[0]!.left_values.position = "Новая позиция";
  const coordinate = target.rows[0]!.cells[0]!.coordinate;
  const rules = { "0:DE": { start: "2026-09-01", end: "2026-09-06", calendar: "USER_CONFIRMED" as const, reason: "Календарь источника" } };
  const decisions = { "2": { include: true, reason: "Отчётный лист" } };
  const { user, referenceReport } = setup({ cancelled: false, sources: { "0:DE99": { ...source, coordinate, metric: "SUPPLIED" } }, target_matrix: target, profile_reused: true,
    applied_period_rules: rules, applied_sheet_decisions: decisions });
  expect(await screen.findByLabelText("Показатель 0:DE99")).toHaveValue("new-projected-position");
  expect(screen.getByText(/Использован сохранённый профиль/)).toBeVisible();
  expect(screen.getByText("Поставка изготовителя")).toBeVisible();
  expect(screen.queryByText("SUPPLIED")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Проверить сопоставление" }));
  expect(referenceReport).toHaveBeenLastCalledWith(expect.objectContaining({ period_rules: rules, sheet_decisions: decisions }));
});

it("permits inclusion reason and resolves a missing manufacturer inside the preview", async () => {
  const { user, referenceReport } = setup({ cancelled: false, sources: {}, recognition: {
    sheets: [{ index: 2, name: "Дополнительный отчёт", state: "hidden", decision_required: true }],
    structural_actions: [{ kind: "UPSERT_POSITION", source_key: "0:9", sheet_index: 0, position: { code: "001", name: "Деталь", manufacturer: "", norm: "2", parent_code: "" }, errors: [{ code: "MANUFACTURER_REQUIRED", message: "Укажите изготовителя" }] }],
  } });
  await user.selectOptions(await screen.findByLabelText("Действие для листа Дополнительный отчёт"), "include");
  await user.type(screen.getByLabelText("Основание включения листа Дополнительный отчёт"), "Согласованная отчётная часть");
  await user.type(screen.getByLabelText("Изготовитель позиции 0:9"), "Завод А");
  await user.type(screen.getByLabelText("Основание исправления позиции 0:9"), "Уточнено автором отчёта");
  await user.click(screen.getByRole("button", { name: "Проверить сопоставление" }));
  expect(referenceReport).toHaveBeenLastCalledWith(expect.objectContaining({
    sheet_decisions: { "2": { include: true, reason: "Согласованная отчётная часть" } },
    structure_overrides: { "0:9": { code: "001", name: "Деталь", manufacturer: "Завод А", norm: "2", parent_code: "", reason: "Уточнено автором отчёта" } },
  }));
});
