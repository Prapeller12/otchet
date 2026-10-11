import { expect, it, vi } from "vitest";
import { PyWebViewGateway } from "../../src/shared/api/pywebview-gateway";
import type { NetworkStatus } from "../../src/shared/api/application-gateway";
import { sourceMatrix } from "../fixtures/source-matrix";
import { draftResult } from "../fixtures/report-draft";

const ready = (overrides: Partial<NetworkStatus> = {}): NetworkStatus => ({ mode: "network", state: "ready", loaded_global_revision: "store:g1", current_global_revision: "store:g1", snapshot_changed: false, can_write: true, message: null, pending_operation_id: null, ...overrides });
const ok = (data: unknown, network = ready()) => ({ ok: true, data, network });
const request = { report_type: "SUBSIDIARY" as const, organization_id: "org", year: 2026, base_revision: "r1", base_global_revision: "store:g1", idempotency_key: "packet-one", changes: [], confirmation: { year: 2026, month: 9 } };

it("pins a draft to its matrix generation even after a status probe sees a new shared head", async () => {
  const stale = ready({ state: "stale", current_global_revision: "store:g2", snapshot_changed: true, can_write: false });
  const matrix = { ...sourceMatrix(), global_revision: "store:g1" };
  const status = vi.fn().mockResolvedValueOnce(ok(ready())).mockResolvedValue(ok(stale, stale));
  const save = vi.fn().mockResolvedValue({ ok: false, error: { code: "GLOBAL_REVISION_CONFLICT", message: "Общие данные изменились" }, network: stale });
  const read = vi.fn().mockResolvedValue(ok(matrix)); const refresh = vi.fn();
  const gateway = new PyWebViewGateway({ get_network_status: status, get_report_matrix: read, save_report_draft: save, refresh_network_data: refresh } as never);
  gateway.setAuthorizationHandler(async (_, execute) => execute({ signer_id: "reviewer", pin: "fresh-code" }));
  await gateway.getNetworkStatus(); await gateway.getReportMatrix({ report_type: "SUBSIDIARY", organization_id: "org" }); await gateway.getNetworkStatus();
  await expect(gateway.saveReportDraft(request)).rejects.toMatchObject({ code: "GLOBAL_REVISION_CONFLICT", outcomeKnown: true });
  expect(save).toHaveBeenCalledWith({ ...request, operation_id: request.idempotency_key, authorization: { signer_id: "reviewer", pin: "fresh-code" } });
  expect(read).toHaveBeenCalledTimes(1); expect(refresh).not.toHaveBeenCalled();
});

it("attaches one stable operation and accepted generation before authorization retries", async () => {
  const write = vi.fn().mockResolvedValueOnce({ ok: false, error: { code: "ACCESS_DENIED", message: "Неверный код" }, network: ready() }).mockResolvedValue(ok({ organization: { id: "new", name: "Завод" } }, ready({ loaded_global_revision: "store:g2", current_global_revision: "store:g2" })));
  const gateway = new PyWebViewGateway({ get_network_status: async () => ok(ready()), create_organization: write } as never);
  gateway.setAuthorizationHandler(async (_, execute) => {
    try { return await execute({ signer_id: "admin", pin: "wrong-code" }); }
    catch { return execute({ signer_id: "admin", pin: "correct-code" }); }
  });
  await gateway.createOrganization("Завод");
  const [first, second] = write.mock.calls.map(call => call[0]);
  expect(first).toMatchObject({ base_global_revision: "store:g1", name: "Завод" });
  expect(second.operation_id).toBe(first.operation_id); expect(second.base_global_revision).toBe(first.base_global_revision);
  expect(second.authorization.pin).toBe("correct-code");
});

it("uses the confirmed generation for the next admin, import and PDF commands", async () => {
  let generation = 1;
  const write = vi.fn(async () => { generation++; return ok({}, ready({ loaded_global_revision: `store:g${generation}`, current_global_revision: `store:g${generation}` })); });
  const gateway = new PyWebViewGateway({ get_network_status: async () => ok(ready()), save_report_layout: write, validate_import: write, commit_import: write, export_pdf: write, create_report_signer: write } as never);
  gateway.setAuthorizationHandler(async (_, execute) => execute({ signer_id: "admin", pin: "fresh-code" }));
  await gateway.saveReportLayout({ report_type: "SUBSIDIARY", organization_id: "org", rows: [] });
  await gateway.validateImport({ report_type: "SUBSIDIARY", organization_id: "org" });
  await gateway.commitImport({ batch_id: "batch" });
  await gateway.exportPdf({ report_type: "SUBSIDIARY", organization_id: "org", month: 9 });
  await gateway.createReportSigner({ display_name: "Иван", pin: "new-user-code" });
  expect(write.mock.calls.map(call => (call as unknown[])[0])).toEqual(expect.arrayContaining([
    expect.objectContaining({ base_global_revision: "store:g1", operation_id: expect.any(String) }),
    expect.objectContaining({ base_global_revision: "store:g5", operation_id: expect.any(String) }),
  ]));
  expect(new Set(write.mock.calls.map(call => ((call as unknown[])[0] as { operation_id: string }).operation_id)).size).toBe(5);
});

it("keeps a lost reply unknown across status probes and missing receipts, then resolves a confirmed receipt", async () => {
  const status = vi.fn(async () => ok(ready())); const write = vi.fn().mockRejectedValue(new Error("Ответ потерян"));
  const recovery = vi.fn().mockImplementationOnce(async ({ operation_id }) => ok({ found: false, operation_id, data_state: "unknown", result: null, global_revision: null, current_global_revision: "store:g1" }))
    .mockImplementationOnce(async ({ operation_id }) => ok({ found: true, operation_id, data_state: "not_committed", result: { ok: false, error: { code: "VALIDATION_ERROR", message: "Не проведено" } }, global_revision: "store:g2", current_global_revision: "store:g2" }, ready({ state: "stale", current_global_revision: "store:g2", snapshot_changed: true, can_write: false })));
  const gateway = new PyWebViewGateway({ get_network_status: status, create_organization: write, get_network_operation_result: recovery } as never);
  let fresh = 0; gateway.setAuthorizationHandler(async (_, execute) => execute({ signer_id: "admin", pin: `fresh-${++fresh}` }));
  await expect(gateway.createOrganization("Завод")).rejects.toMatchObject({ outcomeUnknown: true });
  const pending = await gateway.getNetworkStatus(); expect(pending.state).toBe("outcome_unknown");
  await expect(gateway.createOrganization("Другой завод")).rejects.toMatchObject({ code: "NETWORK_OPERATION_PENDING", outcomeKnown: true }); expect(write).toHaveBeenCalledTimes(1);
  expect((await gateway.getNetworkOperationResult(pending.pending_operation_id!)).data_state).toBe("unknown");
  expect((await gateway.getNetworkStatus()).state).toBe("outcome_unknown");
  expect((await gateway.getNetworkOperationResult(pending.pending_operation_id!)).data_state).toBe("not_committed");
  expect(recovery.mock.calls[0]![0]).toMatchObject({ operation_id: pending.pending_operation_id, authorization: { pin: "fresh-2" } });
  expect(recovery.mock.calls[1]![0].authorization.pin).toBe("fresh-3");
});

it("does not accept a network save without a confirmed publication generation", async () => {
  const result = draftResult(sourceMatrix());
  const gateway = new PyWebViewGateway({ get_network_status: async () => ok(ready()), save_report_draft: async () => ok(result) } as never);
  gateway.setAuthorizationHandler(async (_, execute) => execute({ signer_id: "reviewer", pin: "fresh-code" }));
  await expect(gateway.saveReportDraft(request)).rejects.toMatchObject({ outcomeUnknown: true });
  expect((await gateway.getNetworkStatus()).pending_operation_id).toBe(request.idempotency_key);
});

it.each([{ has_unsaved_changes: true, has_unknown_outcome: false }, { has_unsaved_changes: false, has_unknown_outcome: true }])("blocks unsafe explicit refresh before bridge invocation: %j", async flags => {
  const refresh = vi.fn(); const gateway = new PyWebViewGateway({ refresh_network_data: refresh } as never);
  await expect(gateway.refreshNetworkData(flags)).rejects.toMatchObject({ code: "NETWORK_REFRESH_BLOCKED" }); expect(refresh).not.toHaveBeenCalled();
});

it("retries an ordinary unknown operation with the exact packet, old base and a fresh code", async () => {
  const write = vi.fn().mockRejectedValueOnce(new Error("Ответ потерян")).mockResolvedValue(ok({ organization: { id: "new", name: "Завод" } }, ready({ loaded_global_revision: "store:g2", current_global_revision: "store:g2" })));
  const refresh = vi.fn();
  const gateway = new PyWebViewGateway({ get_network_status: async () => ok(ready()), create_organization: write, refresh_network_data: refresh } as never);
  let fresh = 0; gateway.setAuthorizationHandler(async (_, execute) => execute({ signer_id: "admin", pin: `fresh-${++fresh}` }));
  await expect(gateway.createOrganization("Завод")).rejects.toMatchObject({ outcomeUnknown: true });
  const operationId = (await gateway.getNetworkStatus()).pending_operation_id!;
  expect(gateway.canRetryNetworkOperation(operationId)).toBe(true);
  await expect(gateway.refreshNetworkData({ has_unsaved_changes: false, has_unknown_outcome: false })).rejects.toMatchObject({ code: "NETWORK_REFRESH_BLOCKED" }); expect(refresh).not.toHaveBeenCalled();
  await gateway.retryNetworkOperation(operationId);
  const { authorization: firstAuth, ...first } = write.mock.calls[0]![0]; const { authorization: secondAuth, ...second } = write.mock.calls[1]![0];
  expect(second).toEqual(first); expect(second.base_global_revision).toBe("store:g1"); expect(secondAuth.pin).not.toBe(firstAuth.pin);
  expect((await gateway.getNetworkStatus()).state).toBe("ready");
});

it("does not keep credential-bearing access packets for generic retry", async () => {
  const gateway = new PyWebViewGateway({ get_network_status: async () => ok(ready()), manage_report_signer: async () => { throw new Error("Ответ потерян"); } } as never);
  gateway.setAuthorizationHandler(async (_, execute) => execute({ signer_id: "admin", pin: "fresh-code" }));
  await expect(gateway.manageReportSigner({ action: "change_pin", signer_id: "user", current_pin: "old-code", new_pin: "new-code" })).rejects.toMatchObject({ outcomeUnknown: true });
  expect(gateway.canRetryNetworkOperation((await gateway.getNetworkStatus()).pending_operation_id!)).toBe(false);
});

it("transfers existing protected reports through the native picker with PIN-only authorization", async () => {
  const pristine = ready({ loaded_global_revision: null, current_global_revision: null });
  const transfer = vi.fn(async () => ok({ state: "ready", users: [] }, ready()));
  const gateway = new PyWebViewGateway({ get_network_status: async () => ok(pristine, pristine), import_local_store: transfer } as never);
  await gateway.importLocalStore("existing-admin-code");
  expect(transfer).toHaveBeenCalledWith({ base_global_revision: null, operation_id: expect.any(String), authorization: { pin: "existing-admin-code" } });
});

it("resolves an audit-only failed draft receipt without accepting it as committed data", async () => {
  const failure = { data_state: "not_committed", error: { code: "VALIDATION_ERROR", message: "Проверьте значения" }, global_revision: "store:g2", operation_id: request.idempotency_key };
  const gateway = new PyWebViewGateway({ get_network_status: async () => ok(ready()), save_report_draft: async () => { throw new Error("Ответ потерян"); }, get_report_draft_save_result: async () => ok(failure, ready({ state: "stale", snapshot_changed: true, current_global_revision: "store:g2", can_write: false })) } as never);
  gateway.setAuthorizationHandler(async (_, execute) => execute({ signer_id: "reviewer", pin: "fresh-code" }));
  await expect(gateway.saveReportDraft(request)).rejects.toMatchObject({ outcomeUnknown: true });
  expect(await gateway.getReportDraftSaveResult({ report_type: request.report_type, organization_id: request.organization_id, idempotency_key: request.idempotency_key })).toEqual(failure);
  expect((await gateway.getNetworkStatus()).state).not.toBe("outcome_unknown");
});

it("freezes a packet when the previous local snapshot could not be restored", async () => {
  const gateway = new PyWebViewGateway({ get_network_status: async () => ok(ready()), save_report_draft: async () => ({ ok: false, error: { code: "NETWORK_LOCAL_RECOVERY_REQUIRED", message: "Нужно восстановить локальный снимок" }, network: ready({ state: "outcome_unknown", can_write: false, pending_operation_id: request.idempotency_key }) }) } as never);
  gateway.setAuthorizationHandler(async (_, execute) => execute({ signer_id: "reviewer", pin: "fresh-code" }));
  await expect(gateway.saveReportDraft(request)).rejects.toMatchObject({ code: "NETWORK_LOCAL_RECOVERY_REQUIRED", outcomeUnknown: true });
  expect((await gateway.getNetworkStatus()).pending_operation_id).toBe(request.idempotency_key);
});

it("keeps a network write unknown when the publication status envelope is missing", async () => {
  const gateway = new PyWebViewGateway({ get_network_status: async () => ok(ready()), create_organization: async () => ({ ok: true, data: { organization: { id: "new" } } }) } as never);
  gateway.setAuthorizationHandler(async (_, execute) => execute({ signer_id: "admin", pin: "fresh-code" }));
  await expect(gateway.createOrganization("Завод")).rejects.toMatchObject({ outcomeUnknown: true });
  expect((await gateway.getNetworkStatus()).state).toBe("outcome_unknown");
});

it("does not resolve an unknown draft with a receipt for another operation", async () => {
  const gateway = new PyWebViewGateway({ get_network_status: async () => ok(ready()), save_report_draft: async () => { throw new Error("Ответ потерян"); },
    get_report_draft_save_result: async () => ok({ data_state: "not_committed", error: { code: "VALIDATION_ERROR", message: "Ошибка другого пакета" }, global_revision: "store:g2", operation_id: "other-operation" }) } as never);
  gateway.setAuthorizationHandler(async (_, execute) => execute({ signer_id: "reviewer", pin: "fresh-code" }));
  await expect(gateway.saveReportDraft(request)).rejects.toMatchObject({ outcomeUnknown: true });
  await expect(gateway.getReportDraftSaveResult({ report_type: request.report_type, organization_id: request.organization_id, idempotency_key: request.idempotency_key })).rejects.toThrow("Не удалось распознать результат прежней записи");
  expect((await gateway.getNetworkStatus()).pending_operation_id).toBe(request.idempotency_key);
});

it("uses only readonly recovery routes before an access session exists", async () => {
  const pending = ready({ state: "outcome_unknown", can_write: false, pending_operation_id: "previous-operation" });
  const profiles = vi.fn(async () => ok({ users: [{ id: "manager", display_name: "Автор прежней записи", role: "project_manager" }] }, pending));
  const unlock = vi.fn(async () => ok({ unlocked: true }, pending));
  const recovery = vi.fn(async ({ operation_id }) => ok({ found: false, operation_id, data_state: "unknown", result: null, global_revision: null, current_global_revision: "store:g1" }, pending));
  const access = vi.fn(); const gateway = new PyWebViewGateway({ get_network_recovery_users: profiles, unlock_network_recovery: unlock, get_network_operation_result: recovery, get_access_status: access } as never);
  expect((await gateway.getNetworkRecoveryUsers()).users[0]?.role).toBe("project_manager");
  await gateway.unlockNetworkRecovery({ signer_id: "reviewer", pin: "keyholder-code" });
  expect(unlock).toHaveBeenCalledWith({ authorization: { signer_id: "reviewer", pin: "keyholder-code" } });
  expect((await gateway.recoverNetworkSetupOperation("previous-operation", "owner-code", "manager")).data_state).toBe("unknown");
  expect(recovery).toHaveBeenCalledWith({ operation_id: "previous-operation", authorization: { pin: "owner-code", signer_id: "manager" } }); expect(access).not.toHaveBeenCalled();
});
