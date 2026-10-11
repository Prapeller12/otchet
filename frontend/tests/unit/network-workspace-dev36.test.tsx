import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../src/app/App";
import { ApplicationGatewayProvider } from "../../src/app/providers/ApplicationGatewayProvider";
import { AccessGate } from "../../src/features/access/AccessGate";
import { DemoGateway } from "../../src/shared/api/demo-gateway";
import type { ApplicationGateway, NetworkStatus, ReportMatrixContract, SaveReportDraftRequest } from "../../src/shared/api/application-gateway";
import { ReportMatrix } from "../../src/widgets/report-matrix/ReportMatrix";
import { sourceMatrix } from "../fixtures/source-matrix";
import { draftResult } from "../fixtures/report-draft";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const ready = (overrides: Partial<NetworkStatus> = {}): NetworkStatus => ({ mode: "network", state: "ready", loaded_global_revision: "store:g1", current_global_revision: "store:g1", snapshot_changed: false, can_write: true, message: null, pending_operation_id: null, ...overrides });
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function workspace(overrides: Partial<ApplicationGateway> = {}) {
  const initial = { ...sourceMatrix(), global_revision: "store:g1" };
  const read = vi.fn(async () => initial);
  const gateway = Object.assign(new DemoGateway(), {
    getNetworkStatus: vi.fn(async () => ready()),
    refreshNetworkData: vi.fn(async () => ready()),
    listOrganizations: async () => ({ organizations: [{ id: initial.organization_id, name: "Дочерняя площадка", kind: "SUBSIDIARY" as const }] }),
    getReportMatrix: read,
  }, overrides);
  render(<ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider>);
  return { initial, read, gateway };
}
async function edit(value: string) {
  const user = userEvent.setup();
  const table = await screen.findByRole("table", { name: "Дочерние общества" });
  await user.dblClick(within(table).getAllByRole("button", { name: /доступна для ввода/ })[0]!);
  const input = screen.getByLabelText(/Редактирование:/); await user.clear(input); await user.type(input, value + "{Enter}");
}

it("checks network status without replacing the matrix or its draft generation", async () => {
  const check = vi.fn().mockResolvedValueOnce(ready()).mockResolvedValue(ready({ state: "stale", current_global_revision: "store:g2", snapshot_changed: true, can_write: false }));
  const save = vi.fn().mockRejectedValue(Object.assign(new Error("Общие данные изменились"), { code: "GLOBAL_REVISION_CONFLICT", outcomeKnown: true }));
  const { read, gateway } = workspace({ getNetworkStatus: check, saveReportDraft: save });
  await edit("0"); const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Проверить соединение" }));
  expect(await screen.findByText("В общих данных есть изменения")).toBeVisible(); expect(read).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Обновить общие данные" })).toBeDisabled();
  expect(gateway.refreshNetworkData).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Сохранить (1)" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save.mock.calls[0]![0]).toMatchObject({ base_global_revision: "store:g1", base_revision: expect.any(String), operation_id: expect.any(String), changes: [{ value: { kind: "QUANTITY", quantity: "0" } }] });
  expect(screen.getByRole("alert")).toHaveTextContent("Черновик остался в форме");
  expect(screen.getByRole("button", { name: "Сохранить (1)" })).toBeEnabled();
});

it("keeps the old table visible through refresh and adopts only a fully loaded replacement", async () => {
  const shared = deferred<NetworkStatus>(); const replacement = deferred<ReportMatrixContract>();
  const initial = { ...sourceMatrix(), global_revision: "store:g1" };
  const read = vi.fn().mockResolvedValueOnce(initial).mockImplementation(() => replacement.promise);
  const refresh = vi.fn(() => shared.promise); workspace({ getReportMatrix: read, refreshNetworkData: refresh });
  await screen.findByRole("table", { name: "Дочерние общества" });
  await userEvent.setup().click(screen.getByRole("button", { name: "Обновить общие данные" }));
  expect(screen.getByRole("table", { name: "Дочерние общества" })).toBeVisible(); expect(document.querySelector("main")).toHaveAttribute("inert");
  shared.resolve(ready({ loaded_global_revision: "store:g2", current_global_revision: "store:g2" }));
  await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("table", { name: "Дочерние общества" })).toBeVisible();
  replacement.resolve({ ...initial, title: "Обновлённая форма", global_revision: "store:g2" });
  expect(await screen.findByRole("table", { name: "Обновлённая форма" })).toBeVisible();
  expect(screen.getByText("Общие данные обновлены.")).toBeVisible(); expect(document.querySelector("main")).not.toHaveAttribute("inert");
});

it.each(["refresh", "matrix"])("retains the visible matrix when explicit %s fails", async failure => {
  const initial = { ...sourceMatrix(), global_revision: "store:g1" };
  const read = vi.fn().mockResolvedValueOnce(initial).mockRejectedValue(new Error("Снимок не прочитан"));
  workspace({ getReportMatrix: read, refreshNetworkData: failure === "refresh" ? vi.fn().mockRejectedValue(new Error("Сеть недоступна")) : async () => ready({ loaded_global_revision: "store:g2", current_global_revision: "store:g2" }) });
  await screen.findByRole("table", { name: "Дочерние общества" });
  await userEvent.setup().click(screen.getByRole("button", { name: "Обновить общие данные" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Открытая таблица сохранена");
  expect(screen.getByRole("table", { name: "Дочерние общества" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Обновить общие данные" })).toBeEnabled();
});

it("blocks refresh while a saved packet is uncertain, retaining that packet unchanged for retry", async () => {
  const save = vi.fn().mockRejectedValue(Object.assign(new Error("Ответ потерян"), { outcomeUnknown: true }));
  const result = vi.fn(async () => ({ data_state: "unknown" as const }));
  const { gateway } = workspace({ saveReportDraft: save, getReportDraftSaveResult: result });
  await edit("0"); const user = userEvent.setup(); await user.click(screen.getByRole("button", { name: "Сохранить (1)" }));
  expect(await screen.findByText("Результат сохранения неизвестен")).toBeVisible();
  expect(screen.getByRole("button", { name: "Обновить общие данные" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Проверить результат сохранения" }));
  await user.click(await screen.findByRole("button", { name: "Повторить тот же пакет" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
  expect(save.mock.calls[1]![0]).toEqual(save.mock.calls[0]![0]); expect(gateway.refreshNetworkData).not.toHaveBeenCalled();
});

it("does not overwrite recovered committed values with a pre-publication local read", async () => {
  const initial = { ...sourceMatrix(), global_revision: "store:g1" };
  let packet!: SaveReportDraftRequest;
  const save = vi.fn(async (request: SaveReportDraftRequest) => { packet = request; throw Object.assign(new Error("Ответ потерян"), { outcomeUnknown: true }); });
  const gateway = Object.assign(new DemoGateway(), { saveReportDraft: save, getReportDraftSaveResult: async () => draftResult(initial, packet, { global_revision: "store:g2" }), getReportMatrix: async () => initial });
  function Report() { const [matrix, setMatrix] = useState<ReportMatrixContract>(initial); return <ReportMatrix gateway={gateway} matrix={matrix} onChange={setMatrix} onStatusChange={() => {}} />; }
  render(<Report />); await edit("0"); const user = userEvent.setup(); await user.click(screen.getByRole("button", { name: "Сохранить (1)" }));
  await user.click(await screen.findByRole("button", { name: "Проверить результат сохранения" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Локальный снимок отличается");
  expect(screen.getByLabelText("Состояние сохранения")).toHaveTextContent("Сохранено на сервере; обновите локальные данные");
  const table = screen.getByRole("table", { name: "Дочерние общества" }); await user.dblClick(within(table).getAllByRole("button", { name: /доступна для ввода/ })[0]!);
  expect(screen.getByLabelText(/Редактирование:/)).toHaveValue("0");
});

it("does not show first-run setup for an unavailable share and can retry opening", async () => {
  const status = vi.fn().mockResolvedValueOnce(ready({ state: "unavailable", can_write: false, message: "Общая папка недоступна" })).mockResolvedValue(ready());
  const setup = vi.fn(); const access = vi.fn(async () => ({ state: "ready" as const, users: [] }));
  const gateway = Object.assign(new DemoGateway(), { getNetworkStatus: status, getAccessStatus: access, setupAccess: setup });
  render(<AccessGate gateway={gateway}><p>Рабочие отчёты</p></AccessGate>);
  expect(await screen.findByRole("alert")).toHaveTextContent("Общая папка недоступна");
  expect(access).not.toHaveBeenCalled(); expect(screen.queryByText("Первый запуск")).toBeNull(); expect(setup).not.toHaveBeenCalled();
  await userEvent.setup().click(screen.getByRole("button", { name: "Повторить открытие" }));
  expect(await screen.findByText("Рабочие отчёты")).toBeVisible(); expect(access).toHaveBeenCalledTimes(1);
});

it("offers explicit protected-data transfer only for a pristine shared setup and clears the code", async () => {
  const transfer = vi.fn(async () => ({ cancelled: true as const }));
  const gateway = Object.assign(new DemoGateway(), { getNetworkStatus: async () => ready({ loaded_global_revision: null, current_global_revision: null }), getAccessStatus: async () => ({ state: "setup" as const, users: [] }), importLocalStore: transfer });
  render(<AccessGate gateway={gateway}><p>Рабочие отчёты</p></AccessGate>);
  const button = await screen.findByRole("button", { name: "Перенести существующие локальные отчёты" }); expect(button).toBeDisabled();
  const user = userEvent.setup(); await user.type(screen.getByLabelText("Код администратора (от 6 символов)"), "existing-code"); await user.click(button);
  await waitFor(() => expect(transfer).toHaveBeenCalledWith("existing-code"));
  expect(screen.getByLabelText("Код администратора (от 6 символов)")).toHaveValue("");
  expect(screen.getByText("Первый запуск")).toBeVisible();
});

it("keeps a confirmed transfer separate from local activation and refreshes it only explicitly", async () => {
  let refreshed = false;
  const refresh = vi.fn(async () => { refreshed = true; return ready(); });
  const gateway = Object.assign(new DemoGateway(), { getNetworkStatus: async () => ready({ loaded_global_revision: null, current_global_revision: null }),
    getAccessStatus: async () => ({ state: refreshed ? "ready" as const : "setup" as const, users: [] }),
    importLocalStore: async () => ({ state: "ready" as const, users: [], local_activation_warning: "Локальная копия ещё не принята." }), refreshNetworkData: refresh });
  render(<AccessGate gateway={gateway}><p>Рабочие отчёты</p></AccessGate>);
  const user = userEvent.setup(); await screen.findByText("Первый запуск");
  await user.type(screen.getByLabelText("Код администратора (от 6 символов)"), "existing-code");
  await user.click(screen.getByRole("button", { name: "Перенести существующие локальные отчёты" }));
  expect(await screen.findByText(/Сохранено на сервере; обновите локальные данные/)).toBeVisible();
  expect(screen.queryByText("Рабочие отчёты")).toBeNull(); expect(refresh).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Обновить локальные данные" }));
  expect(await screen.findByText("Рабочие отчёты")).toBeVisible(); expect(refresh).toHaveBeenCalledTimes(1);
});

it.each(["setup", "project_manager"])("can recover a pending %s after restart before access status or Workspace exists", async owner => {
  const pending = ready({ state: "outcome_unknown", can_write: false, pending_operation_id: "previous-operation", loaded_global_revision: owner === "setup" ? null : "store:g1", message: "Проверьте прежнюю запись" });
  const access = vi.fn(async () => ({ state: "ready" as const, users: [] }));
  const recover = vi.fn(async () => ({ found: true, operation_id: "previous-operation", data_state: "committed" as const, result: { ok: true, data: {} }, global_revision: "store:g2", current_global_revision: "store:g2" }));
  const profiles = vi.fn(async () => ({ users: owner === "setup" ? [] : [{ id: "manager", display_name: "Автор незавершённой операции", role: "project_manager" as const }] }));
  const gateway = Object.assign(new DemoGateway(), { getNetworkStatus: async () => pending, getAccessStatus: access, getNetworkRecoveryUsers: profiles, recoverNetworkSetupOperation: recover, refreshNetworkData: async () => ready() });
  render(<AccessGate gateway={gateway}><p>Рабочие отчёты</p></AccessGate>);
  await screen.findByRole("button", { name: "Проверить результат прежней записи" }); expect(access).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Создать администратора и начать" })).toBeNull();
  const user = userEvent.setup();
  if (owner === "project_manager") expect(screen.getByRole("option", { name: /Автор незавершённой операции/ })).toBeVisible();
  await user.type(screen.getByLabelText(owner === "setup" ? "Код администратора" : "Код доступа"), "fresh-owner-code");
  await user.click(screen.getByRole("button", { name: "Проверить результат прежней записи" }));
  expect(recover).toHaveBeenCalledWith("previous-operation", "fresh-owner-code", owner === "setup" ? undefined : "manager");
  expect(await screen.findByText(/Сохранено на сервере; обновите локальные данные/)).toBeVisible(); expect(access).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Обновить локальные данные" }));
  expect(await screen.findByText("Рабочие отчёты")).toBeVisible(); expect(access).toHaveBeenCalledTimes(1);
});

it("unfreezes a known failed draft while preserving values and its original base", async () => {
  const save = vi.fn().mockRejectedValue(Object.assign(new Error("Ответ потерян"), { outcomeUnknown: true }));
  const result = vi.fn(async () => ({ data_state: "not_committed" as const, error: { code: "VALIDATION_ERROR", message: "Проверьте значения" }, global_revision: "store:g2", operation_id: "previous-operation" }));
  workspace({ saveReportDraft: save, getReportDraftSaveResult: result }); await edit("0"); const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Сохранить (1)" }));
  await user.click(await screen.findByRole("button", { name: "Проверить результат сохранения" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Проверьте значения");
  expect(screen.getByLabelText("Состояние сохранения")).toHaveTextContent("Черновик не проведён");
  expect(screen.getByRole("button", { name: "Сохранить (1)" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "Сохранить (1)" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2)); expect(save.mock.calls[1]![0]).toMatchObject({ base_global_revision: "store:g1", changes: [{ value: { kind: "QUANTITY", quantity: "0" } }] });
});

it("opens a recovery snapshot with a keyholder, then requests the exact owner's fresh code again", async () => {
  const pending = ready({ state: "outcome_unknown", can_write: false, pending_operation_id: "previous-operation" });
  const recover = vi.fn().mockRejectedValueOnce(Object.assign(new Error("Откройте снимок ключом ответственного"), { code: "NETWORK_RECOVERY_UNLOCK_REQUIRED" }))
    .mockResolvedValue({ found: true, operation_id: "previous-operation", data_state: "committed", result: { ok: true, data: {} }, global_revision: "store:g2", current_global_revision: "store:g2" });
  const unlock = vi.fn(async () => ({ unlocked: true as const })); const access = vi.fn();
  const gateway = Object.assign(new DemoGateway(), { getNetworkStatus: async () => pending, getAccessStatus: access,
    getNetworkRecoveryUsers: async () => ({ users: [{ id: "manager", display_name: "Автор прежней записи", role: "project_manager" as const }, { id: "reviewer", display_name: "Проверяющий", role: "reviewer" as const }] }),
    recoverNetworkSetupOperation: recover, unlockNetworkRecovery: unlock });
  render(<AccessGate gateway={gateway}><p>Рабочие отчёты</p></AccessGate>); const user = userEvent.setup();
  await screen.findByRole("button", { name: "Проверить результат прежней записи" });
  await user.type(screen.getByLabelText("Код доступа"), "first-owner-code");
  await user.click(screen.getByRole("button", { name: "Проверить результат прежней записи" }));
  const open = await screen.findByRole("button", { name: "Открыть снимок для проверки" });
  expect(screen.getByLabelText("Код доступа")).toHaveValue(""); expect(open).toBeDisabled();
  await user.type(screen.getByLabelText("Код для открытия снимка"), "fresh-keyholder-code"); await user.click(open);
  expect(unlock).toHaveBeenCalledWith({ signer_id: "reviewer", pin: "fresh-keyholder-code" });
  expect(await screen.findByText(/Теперь снова введите код точного владельца/)).toBeVisible();
  expect(screen.queryByLabelText("Код для открытия снимка")).toBeNull();
  expect(screen.getByRole("button", { name: "Проверить результат прежней записи" })).toBeDisabled();
  await user.type(screen.getByLabelText("Код доступа"), "second-owner-code");
  await user.click(screen.getByRole("button", { name: "Проверить результат прежней записи" }));
  expect(recover.mock.calls.map(call => call.slice(1))).toEqual([["first-owner-code", "manager"], ["second-owner-code", "manager"]]);
  expect(screen.getByText(/Сохранено на сервере; обновите локальные данные/)).toBeVisible(); expect(access).not.toHaveBeenCalled();
});

it("distinguishes a confirmed PDF receipt from a delivered file and never automatically exports again", async () => {
  const pdf = vi.fn(); const refresh = vi.fn();
  const recovery = vi.fn(async () => ({ found: true, operation_id: "previous-pdf", data_state: "committed" as const, result: { ok: true, data: { export_state: "pending" } }, global_revision: "store:g2", current_global_revision: "store:g2" }));
  workspace({ getNetworkOperationResult: recovery, exportPdf: pdf, refreshNetworkData: refresh });
  await screen.findByRole("table", { name: "Дочерние общества" });
  act(() => window.dispatchEvent(new CustomEvent("network-status-changed", { detail: ready({ state: "outcome_unknown", can_write: false, pending_operation_id: "previous-pdf" }) })));
  await userEvent.setup().click(screen.getByRole("button", { name: "Проверить общую запись" }));
  expect(await screen.findByText(/Файл PDF ещё не получен/)).toHaveTextContent("явно выполните «Печать / PDF А4» повторно");
  expect(screen.queryByText(/PDF сохранён/)).toBeNull(); expect(pdf).not.toHaveBeenCalled(); expect(refresh).not.toHaveBeenCalled();
  expect(screen.getByRole("table", { name: "Дочерние общества" })).toBeVisible();
});

it("keeps startup readiness false until both network and access reads settle", async () => {
  const network = deferred<NetworkStatus>(); const access = deferred<{ state: "setup"; users: [] }>();
  const readAccess = vi.fn(() => access.promise);
  const gateway = Object.assign(new DemoGateway(), { getNetworkStatus: () => network.promise, getAccessStatus: readAccess });
  render(<AccessGate gateway={gateway}><p>Рабочие отчёты</p></AccessGate>);
  expect(screen.getByRole("main")).toHaveAttribute("data-startup-ready", "false"); expect(readAccess).not.toHaveBeenCalled();
  act(() => network.resolve(ready())); await waitFor(() => expect(readAccess).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("main")).toHaveAttribute("data-startup-ready", "false");
  act(() => access.resolve({ state: "setup", users: [] }));
  await screen.findByText("Первый запуск");
  expect(screen.getByRole("main")).toHaveAttribute("data-startup-ready", "true");
});

it("marks an actionable pending restart ready only after recovery profiles settle", async () => {
  const profiles = deferred<{ users: [] }>(); const readAccess = vi.fn();
  const gateway = Object.assign(new DemoGateway(), { getNetworkStatus: async () => ready({ state: "outcome_unknown", can_write: false, pending_operation_id: "previous-operation" }),
    getAccessStatus: readAccess, getNetworkRecoveryUsers: () => profiles.promise, recoverNetworkSetupOperation: vi.fn() });
  render(<AccessGate gateway={gateway}><p>Рабочие отчёты</p></AccessGate>);
  expect(screen.getByRole("main")).toHaveAttribute("data-startup-ready", "false");
  await screen.findByText("Проверить прежнюю общую запись");
  expect(screen.getByRole("main")).toHaveAttribute("data-startup-ready", "false");
  act(() => profiles.resolve({ users: [] })); await screen.findByRole("alert");
  expect(screen.getByRole("main")).toHaveAttribute("data-startup-ready", "true");
  expect(screen.getByLabelText("Код администратора")).toBeEnabled();
  expect(screen.getByRole("button", { name: "Проверить результат прежней записи" })).toBeVisible(); expect(readAccess).not.toHaveBeenCalled();
});

it("marks a genuine startup error ready with its retry action after the failed read settles", async () => {
  const network = deferred<NetworkStatus>(); const gateway = Object.assign(new DemoGateway(), { getNetworkStatus: () => network.promise, getAccessStatus: vi.fn(async () => ({ state: "setup" as const, users: [] })) });
  render(<AccessGate gateway={gateway}><p>Рабочие отчёты</p></AccessGate>);
  expect(screen.getByRole("main")).toHaveAttribute("data-startup-ready", "false");
  act(() => network.reject(new Error("Общая папка недоступна")));
  expect(await screen.findByRole("alert")).toHaveTextContent("Общая папка недоступна");
  expect(screen.getByRole("main")).toHaveAttribute("data-startup-ready", "true");
  expect(screen.getByRole("button", { name: "Повторить открытие" })).toBeEnabled();
});

it("opens the existing gateway fallback when no access-status method is provided", () => {
  const network = vi.fn(); const gateway = Object.assign(new DemoGateway(), { getNetworkStatus: network });
  render(<AccessGate gateway={gateway}><main className="report-matrix">Рабочие отчёты</main></AccessGate>);
  expect(screen.getByText("Рабочие отчёты")).toBeVisible(); expect(document.querySelector(".access-page")).toBeNull(); expect(network).not.toHaveBeenCalled();
});
