import { useRef, useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { ApplicationGateway, DraftNavigation, ReportLayoutContract, ReportMatrixContract, SaveReportDraftRequest, SaveReportLayoutRequest } from "../../src/shared/api/application-gateway";
import { DemoGateway } from "../../src/shared/api/demo-gateway";
import { ReportMatrix } from "../../src/widgets/report-matrix/ReportMatrix";
import { sourceMatrix } from "../fixtures/source-matrix";
import { draftResult } from "../fixtures/report-draft";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function show(initial: ReportMatrixContract, gateway: ApplicationGateway, mode: "entry" | "admin" = "admin") {
  const moved = vi.fn();
  function Workspace() {
    const [matrix, setMatrix] = useState(initial);
    const navigation = useRef<DraftNavigation | null>(null);
    return <><button onClick={() => navigation.current?.request(moved)}>Перейти в другую форму</button><ReportMatrix workspaceMode={mode} gateway={gateway} matrix={matrix} onChange={setMatrix} onStatusChange={() => {}} onNavigationReady={value => { navigation.current = value; }} /></>;
  }
  render(<Workspace />); return moved;
}
async function editReceipt(value: string) {
  const user = userEvent.setup();
  const table = screen.getByRole("table", { name: "Дочерние общества" });
  const first = within(table).getAllByRole("button", { name: /доступна для ввода/ })[0]!;
  await user.dblClick(first); const input = screen.getByLabelText(/Редактирование:/); await user.clear(input); await user.type(input, value + "{Enter}");
}
it("writes header, plan, release and primary cells as one revision checked packet", async () => {
  const initial = sourceMatrix();
  const legacy = vi.fn();
  const save = vi.fn(async (request: SaveReportDraftRequest) => draftResult(initial, request));
  const gateway = Object.assign(new DemoGateway(), { saveReportDraft: save, saveReportPresentation: legacy, saveReportCells: legacy, getReportMatrix: vi.fn(async () => initial) });
  show(initial, gateway); const user = userEvent.setup();
  await user.type(screen.getByLabelText("Наименование изделия"), "Изделие");
  await user.type(screen.getByLabelText("План выпуска, шт."), "100");
  await user.type(screen.getByLabelText("Выпущено, шт."), "10");
  await editReceipt("0");
  await user.click(screen.getByRole("button", { name: "Сохранить (1)" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save.mock.calls[0]![0]).toMatchObject({ base_revision: initial.matrix_revision, confirmation: { year: 2026, month: 9 }, presentation: { header: { product_name: "Изделие" }, plans: { "2026-09": "100" }, actuals: { "2026-09": "10" } }, changes: [{ value: { kind: "QUANTITY", quantity: "0" } }] });
  expect(legacy).not.toHaveBeenCalled();
});
it("offers stay, discard and save-and-go, and keeps the draft after revision conflict", async () => {
  const initial = sourceMatrix();
  const save = vi.fn().mockRejectedValueOnce(new Error("REVISION_CONFLICT: отчёт изменён")).mockImplementation(async (request: SaveReportDraftRequest) => draftResult(initial, request));
  const moved = show(initial, Object.assign(new DemoGateway(), { saveReportDraft: save, getReportMatrix: vi.fn(async () => initial) }));
  await editReceipt("0"); const user = userEvent.setup();
  await user.click(screen.getByText("Перейти в другую форму"));
  await user.click(screen.getByRole("button", { name: "Остаться" })); expect(moved).not.toHaveBeenCalled();
  await user.click(screen.getByText("Перейти в другую форму"));
  await user.click(screen.getByRole("button", { name: "Сохранить и перейти" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1)); expect(moved).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toHaveTextContent("REVISION_CONFLICT");
  await user.click(screen.getByRole("button", { name: "Сохранить и перейти" })); await waitFor(() => expect(moved).toHaveBeenCalledTimes(1));
});
it("restores all draft sections when discarding before navigation", async () => {
  const initial = sourceMatrix(); const save = vi.fn();
  const moved = show(initial, Object.assign(new DemoGateway(), { saveReportDraft: save }));
  const user = userEvent.setup(); await user.type(screen.getByLabelText("Наименование изделия"), "Не сохранять"); await user.type(screen.getByLabelText("Выпущено, шт."), "100"); await editReceipt("0");
  await user.click(screen.getByText("Перейти в другую форму")); await user.click(screen.getByRole("button", { name: "Отбросить и перейти" }));
  expect(moved).toHaveBeenCalledTimes(1); expect(save).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Наименование изделия")).toHaveValue(initial.presentation?.header?.product_name ?? ""); expect(screen.getByLabelText("Выпущено, шт.")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
});
it("freezes uncertain writes and reconciles by the same durable operation ID", async () => {
  const initial = sourceMatrix(); const save = vi.fn().mockRejectedValue(Object.assign(new Error("Ответ потерян"), { outcomeUnknown: true }));
  const recover = vi.fn(async (_request: SaveReportDraftRequest) => draftResult(initial));
  const moved = show(initial, Object.assign(new DemoGateway(), { saveReportDraft: save, getReportDraftSaveResult: recover, getReportMatrix: vi.fn(async () => initial) }));
  await editReceipt("0"); const user = userEvent.setup(); await user.click(screen.getByRole("button", { name: "Сохранить (1)" }));
  expect(await screen.findByText("Результат сохранения неизвестен")).toBeVisible();
  expect(screen.getByRole("button", { name: "Сохранить (1)" })).toBeDisabled();
  await user.click(screen.getByText("Перейти в другую форму")); expect(moved).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Проверить результат сохранения" }));
  await waitFor(() => expect(recover).toHaveBeenCalledTimes(1)); expect(recover.mock.calls[0]![0]).toMatchObject({ idempotency_key: save.mock.calls[0]![0].idempotency_key });
  expect(save).toHaveBeenCalledTimes(1); await waitFor(() => expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled());
});
it("clears committed drafts while reporting signature and post-backup failures separately", async () => {
  const initial = sourceMatrix(); const save = vi.fn(async (request: SaveReportDraftRequest) => draftResult(initial, request, { verification_state: "verification_error", verification_error: { code: "SIGNATURE_FAILED", message: "Подпись не создана" }, backup_state: "post_backup_warning", backup_warning: "Копия не создана" }));
  show(initial, Object.assign(new DemoGateway(), { saveReportDraft: save, getReportMatrix: vi.fn(async () => initial) })); await editReceipt("0");
  await userEvent.setup().click(screen.getByRole("button", { name: "Сохранить (1)" }));
  await waitFor(() => expect(screen.getByLabelText("Состояние сохранения")).toHaveTextContent("Данные сохранены"));
  expect(screen.getByLabelText("Состояние сохранения")).toHaveTextContent("отчёт не подтверждён"); expect(screen.getByLabelText("Состояние сохранения")).toHaveTextContent("резервная копия не создана"); expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
});
it("highlights matches without hiding canonical rows and advances to a visible error", async () => {
  const initial = sourceMatrix(); initial.rows[0]!.cells[1]!.issue = { code: "MISSING", message: "Заполнить" };
  show(initial, new DemoGateway()); const before = document.querySelectorAll("[data-matrix-row]").length;
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: initial.rows[0]!.left_values.party } });
  expect(document.querySelectorAll("[data-matrix-row]").length).toBe(before); expect(document.querySelectorAll(".matrix-search-match").length).toBeGreaterThan(0);
  await userEvent.setup().click(screen.getByRole("button", { name: "Следующая ошибка" })); expect(screen.queryByRole("dialog")).toBeNull();
});
it("retries a missing uncertain result with the identical packet and operation key", async () => {
  const initial = sourceMatrix();
  const save = vi.fn().mockRejectedValueOnce(Object.assign(new Error("Ответ потерян"), { outcomeUnknown: true })).mockImplementation(async (request: SaveReportDraftRequest) => draftResult(initial, request));
  const recover = vi.fn(async () => ({ data_state: "unknown" as const }));
  show(initial, Object.assign(new DemoGateway(), { saveReportDraft: save, getReportDraftSaveResult: recover, getReportMatrix: vi.fn(async () => initial) }));
  await editReceipt("0"); const user = userEvent.setup(); await user.click(screen.getByRole("button", { name: "Сохранить (1)" }));
  await user.click(await screen.findByRole("button", { name: "Проверить результат сохранения" }));
  await user.click(await screen.findByRole("button", { name: "Повторить тот же пакет" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2)); expect(save.mock.calls[1]![0]).toEqual(save.mock.calls[0]![0]); expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
});

it("keeps zero distinct from an empty cell and removes a draft restored to its saved value", async () => {
  const initial = sourceMatrix(); const save = vi.fn(); show(initial, Object.assign(new DemoGateway(), { saveReportDraft: save }));
  await editReceipt("0"); expect(screen.getByRole("button", { name: "Сохранить (1)" })).toBeEnabled();
  await editReceipt("120"); expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  const user = userEvent.setup(); const table = screen.getByRole("table", { name: "Дочерние общества" }); await user.dblClick(within(table).getAllByRole("button", { name: /доступна для ввода/ })[0]!); await user.clear(screen.getByLabelText(/Редактирование:/)); await user.keyboard("{Enter}");
  expect(screen.getByRole("button", { name: "Сохранить (1)" })).toBeEnabled(); expect(save).not.toHaveBeenCalled();
});
it.each(["recover", "retry"])("keeps committed state truthful if refresh fails after %s", async action => {
  const initial = sourceMatrix(); const save = vi.fn().mockRejectedValueOnce(Object.assign(new Error("Ответ потерян"), { outcomeUnknown: true })).mockImplementation(async (request: SaveReportDraftRequest) => draftResult(initial, request));
  const recover = vi.fn(async () => action === "recover" ? draftResult(initial) : { data_state: "unknown" as const });
  show(initial, Object.assign(new DemoGateway(), { saveReportDraft: save, getReportDraftSaveResult: recover, getReportMatrix: vi.fn().mockRejectedValue(new Error("Не прочитано")) }));
  await editReceipt("0"); const user = userEvent.setup(); await user.click(screen.getByRole("button", { name: "Сохранить (1)" })); await user.click(await screen.findByRole("button", { name: "Проверить результат сохранения" }));
  if (action === "retry") await user.click(await screen.findByRole("button", { name: "Повторить тот же пакет" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Данные сохранены. Не удалось обновить расчёты"));
  expect(screen.queryByRole("button", { name: "Проверить результат сохранения" })).toBeNull(); expect(screen.getByLabelText("Состояние сохранения")).toHaveTextContent("Данные сохранены");
});
it.each(["header", "controls", "uncertain title"])("blocks import, export and structural removal during a %s draft", async section => {
  const initial = sourceMatrix(); initial.rows[0]!.workspace_id = "detail-a"; initial.rows[0]!.supplier_id = "supplier-a";
  initial.capabilities.import = { enabled: true }; initial.capabilities.export = { enabled: true };
  const save = vi.fn().mockRejectedValue(Object.assign(new Error("Ответ потерян"), { outcomeUnknown: true }));
  show(initial, Object.assign(new DemoGateway(), { saveReportDraft: save })); const user = userEvent.setup();
  if (section === "header") await user.type(screen.getByLabelText("Наименование изделия"), "Черновик");
  else if (section === "controls") await user.type(screen.getByLabelText("Выпущено, шт."), "10");
  else {
    await user.click(screen.getByRole("button", { name: "Переименовать отчёт" })); await user.type(screen.getByLabelText("Название отчёта"), " Черновик");
    await user.click(screen.getByRole("button", { name: "Применить название" })); await screen.findByText("Результат сохранения неизвестен");
  }
  expect(screen.getByRole("button", { name: "Импорт Excel" })).toBeDisabled(); expect(screen.getByRole("button", { name: "Экспорт Excel" })).toBeDisabled(); expect(screen.getByRole("button", { name: "Убрать производителя" })).toBeDisabled();
});
it.each(["import", "remove supplier"])("uses the refreshed saved baseline after %s for discard and the next draft save", async operation => {
  const initial = sourceMatrix(); initial.rows[0]!.workspace_id = "detail-a"; initial.rows[0]!.supplier_id = "supplier-a";
  initial.capabilities.import = { enabled: true };
  const refreshed = structuredClone(initial); refreshed.matrix_revision = `${operation}-r2`;
  if (operation === "remove supplier") refreshed.rows.shift();
  refreshed.rows[0]!.cells[1]!.value = { kind: "QUANTITY", quantity: "450" };
  const layout: ReportLayoutContract = { report_type: initial.report_type, organization_id: initial.organization_id, templates: [], rows: [{
    id: "detail-a", template_group_id: "component", party_name: "", position_name: "Деталь А", configuration: { category: "", image: "", norm: "2", opening: "", indicators: [], subsidiary: { number: "1", designation: "ABC", suppliers: [{ id: "supplier-a", name: "Завод 1", contract: "", archived: false }] } },
  }] };
  const save = vi.fn(async (request: SaveReportDraftRequest) => draftResult(refreshed, request));
  const saveLayout = vi.fn(async (request: SaveReportLayoutRequest) => ({ ...layout, rows: request.rows }));
  const gateway = Object.assign(new DemoGateway(), {
    saveReportDraft: save, getReportMatrix: vi.fn(async () => refreshed), getReportLayout: vi.fn(async () => layout), saveReportLayout: saveLayout,
    validateImport: vi.fn(async () => ({ cancelled: false, batch_id: "synthetic-batch", file_name: "synthetic.xlsx", status: "STAGED" as const, error_count: 0, issues: [] })),
    commitImport: vi.fn(async () => ({ batch_id: "synthetic-batch", status: "COMMITTED" as const, imported_count: 1, same_count: 0, backup_file: "synthetic.sqlite3", already_committed: false })),
  });
  const moved = show(initial, gateway); const user = userEvent.setup();
  if (operation === "import") {
    await user.click(screen.getByRole("button", { name: "Импорт Excel" })); await screen.findByRole("dialog", { name: "Проверка импорта Excel" });
    await user.click(screen.getByLabelText(/Я просмотрел весь пакет/)); await user.click(screen.getByRole("button", { name: "Подтвердить импорт" })); await screen.findByText(/Импорт завершён/);
  } else {
    vi.spyOn(window, "confirm").mockReturnValue(true); await user.click(screen.getByRole("button", { name: "Убрать производителя" }));
    await waitFor(() => expect(saveLayout).toHaveBeenCalledTimes(1)); expect(saveLayout.mock.calls[0]![0].rows[0]!.configuration!.subsidiary!.suppliers[0]!.archived).toBe(true);
  }
  await waitFor(() => expect(screen.getByRole("button", { name: "значение 450, доступна для ввода" })).toBeEnabled());
  const savedRowCount = document.querySelectorAll("[data-matrix-row]").length;
  await editReceipt("451"); await user.click(screen.getByText("Перейти в другую форму")); await user.click(screen.getByRole("button", { name: "Отбросить и перейти" }));
  expect(moved).toHaveBeenCalledTimes(1); expect(document.querySelectorAll("[data-matrix-row]")).toHaveLength(savedRowCount);
  expect(screen.getByRole("button", { name: "значение 450, доступна для ввода" })).toBeVisible(); expect(screen.queryByRole("button", { name: "значение 451, доступна для ввода" })).toBeNull();
  await editReceipt("452"); await user.click(screen.getByRole("button", { name: "Сохранить (1)" })); await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save.mock.calls[0]![0]).toMatchObject({ base_revision: refreshed.matrix_revision, changes: [{ coordinate: refreshed.rows[0]!.cells[1]!.coordinate, value: { kind: "QUANTITY", quantity: "452" } }] });
});
