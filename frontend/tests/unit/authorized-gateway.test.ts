import { expect, it, vi } from "vitest";
import { PyWebViewGateway } from "../../src/shared/api/pywebview-gateway";
import type { AuthorizationHandler } from "../../src/shared/api/application-gateway";

it("refuses writes without a mounted confirmation UI and requires a fresh code for each save", async () => {
  const save = vi.fn(async () => ({ ok: true, data: { actuals: { "2026-01": "7" } } }));
  const gateway = new PyWebViewGateway({ save_report_presentation: save } as never);
  const request = { report_type: "SUBSIDIARY" as const, organization_id: "org", actuals: { "2026-01": "7" } };
  await expect(gateway.saveReportPresentation(request)).rejects.toThrow("Подтверждение записи недоступно");
  expect(save).not.toHaveBeenCalled();
  let count = 0;
  const authorize = vi.fn<AuthorizationHandler>(async (_prompt, execute) => execute({ signer_id: "reviewer", pin: `code-${++count}` }));
  gateway.setAuthorizationHandler(authorize);
  await gateway.saveReportPresentation(request);
  await gateway.saveReportPresentation(request);
  expect(authorize).toHaveBeenCalledTimes(2);
  expect(save.mock.calls).toEqual([
    [{ ...request, authorization: { signer_id: "reviewer", pin: "code-1" } }],
    [{ ...request, authorization: { signer_id: "reviewer", pin: "code-2" } }],
  ]);
  gateway.setAuthorizationHandler(null);
  await expect(gateway.saveReportPresentation(request)).rejects.toThrow("Подтверждение записи недоступно");
  expect(save).toHaveBeenCalledTimes(2);
});

it("keeps configuration, organizations and workbook changes admin-only while responsible persons may edit plans", async () => {
  const success = vi.fn(async () => ({ ok: true, data: {} }));
  const gateway = new PyWebViewGateway({ save_report_presentation: success, create_organization: success, save_report_layout: success, reference_report: success } as never);
  const authorize = vi.fn<AuthorizationHandler>(async (_prompt, execute) => execute({ signer_id: "admin", pin: "secret" }));
  gateway.setAuthorizationHandler(authorize);
  await gateway.createOrganization("Завод");
  await gateway.saveReportLayout({ report_type: "HEAD_SITE", organization_id: "org", rows: [] });
  await gateway.saveReportPresentation({ report_type: "HEAD_SITE", organization_id: "org", plans: { "2026-01": "10" } });
  await gateway.referenceReport({ action: "save", organization_id: "org", id: "book", changes: [] });
  expect(authorize).toHaveBeenCalledTimes(4);
  expect(authorize.mock.calls.map(([prompt]) => prompt.adminOnly)).toEqual([true, true, false, true]);
});

it("uses the signature code for write authorization without requesting a second code", async () => {
  const verify = vi.fn(async () => ({ ok: true, data: { status: "VERIFIED" } }));
  const gateway = new PyWebViewGateway({ verify_report: verify } as never);
  const request = { report_type: "HEAD_SITE" as const, organization_id: "org", month: 1, signer_id: "reviewer", pin: "secret", snapshot_sha256: "hash", confirmed: true };
  await gateway.verifyReport(request);
  expect(verify).toHaveBeenCalledWith({ ...request, authorization: { signer_id: "reviewer", pin: "secret" } });
});

it("requires administrator code for organization archive and responsible code for plan cells", async () => {
  const archive = vi.fn(async () => ({ ok: true, data: { organizations: [] } }));
  const save = vi.fn(async () => ({ ok: true, data: { matrix_revision: "next", cells: [] } }));
  const gateway = new PyWebViewGateway({ archive_organization: archive, save_report_cells: save } as never);
  const authorize = vi.fn<AuthorizationHandler>(async (_prompt, execute) => execute({ signer_id: "admin", pin: "secret" }));
  await expect(gateway.archiveOrganization("org")).rejects.toThrow("Подтверждение записи недоступно");
  expect(archive).not.toHaveBeenCalled();
  gateway.setAuthorizationHandler(authorize);
  await gateway.archiveOrganization("org");
  await gateway.saveReportCells({ report_type: "DAILY_MOVEMENT", organization_id: "org", base_revision: "old", idempotency_key: "save1", changes: [{ coordinate: { report_type: "DAILY_MOVEMENT", organization_id: "org", product_id: "product", operation_date: "2026-01-01", metric_code: "PRODUCT_PLAN" }, value: { kind: "QUANTITY", quantity: "10" } }] });
  expect(authorize).toHaveBeenCalledTimes(2);
  expect(authorize.mock.calls.map(([prompt]) => prompt.adminOnly)).toEqual([true, false]);
  expect(archive).toHaveBeenCalledWith({ organization_id: "org", authorization: { signer_id: "admin", pin: "secret" } });
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ authorization: { signer_id: "admin", pin: "secret" } }));
});


it("creates profiles using the server administrator session without another code prompt", async () => {
  const create = vi.fn(async () => ({ ok: true, data: { id: "reviewer" } }));
  const end = vi.fn(async () => ({ ok: true, data: { ended: true } }));
  const gateway = new PyWebViewGateway({ create_report_signer: create, end_administration: end } as never);
  const authorize = vi.fn<AuthorizationHandler>();
  gateway.setAuthorizationHandler(authorize);
  const request = { display_name: "Иван", pin: "new-code", role: "reviewer" as const };
  await gateway.createReportSigner(request);
  expect(authorize).not.toHaveBeenCalled();
  expect(create).toHaveBeenCalledWith(request);
  await gateway.endAdministration();
  expect(end).toHaveBeenCalledWith({});
});

it("requires one fresh responsible code before each print and preserves its period", async () => {
  const print = vi.fn(async () => ({ ok: true, data: { cancelled: false } }));
  const gateway = new PyWebViewGateway({ export_pdf: print } as never);
  const authorize = vi.fn<AuthorizationHandler>(async (_prompt, execute) => execute({ signer_id: "manager", pin: "secret" }));
  const request = { report_type: "HEAD_SITE" as const, organization_id: "org", year: 2026, month: 9 };
  await expect(gateway.exportPdf(request)).rejects.toThrow("Подтверждение записи недоступно");
  expect(print).not.toHaveBeenCalled();
  gateway.setAuthorizationHandler(authorize);
  await gateway.exportPdf(request);
  expect(authorize).toHaveBeenCalledTimes(1);
  expect(authorize.mock.calls[0]![0]).toMatchObject({ adminOnly: false, confirmLabel: "Подтвердить и печатать" });
  expect(print).toHaveBeenCalledWith({ ...request, authorization: { signer_id: "manager", pin: "secret" } });
});

it("keeps confirmation period and saved-but-unverified result across the bridge", async () => {
  const result = { matrix_revision: "saved", cells: [], verification_error: { code: "INCOMPLETE", message: "Заполните отчёт" } };
  const save = vi.fn(async () => ({ ok: true, data: result }));
  const gateway = new PyWebViewGateway({ save_report_cells: save } as never);
  gateway.setAuthorizationHandler(async (_prompt, execute) => execute({ signer_id: "manager", pin: "secret" }));
  const request = { report_type: "DAILY_MOVEMENT" as const, organization_id: "org", base_revision: "old", idempotency_key: "one", changes: [], confirmation: { year: 2026, month: 9 } };
  expect(await gateway.saveReportCells(request)).toEqual(result);
  expect(save).toHaveBeenCalledWith({ ...request, authorization: { signer_id: "manager", pin: "secret" } });
});

it("preserves one authorized draft packet and reads its result with a separate fresh code", async () => {
  const result = { matrix_revision: "r2", operation_revision: "r2", current_revision: "r2", data_state: "committed", idempotent_replay: false, cells: [], presentation: {}, verification_state: "verification_error", backup_state: "post_backup_warning" };
  const save = vi.fn(async () => ({ ok: true, data: result })); const recover = vi.fn(async () => ({ ok: true, data: result }));
  const gateway = new PyWebViewGateway({ save_report_draft: save, get_report_draft_save_result: recover } as never);
  let code = 0; const auth = vi.fn<AuthorizationHandler>(async (_prompt, execute) => execute({ signer_id: "reviewer", pin: `fresh-${++code}` })); gateway.setAuthorizationHandler(auth);
  const request = { report_type: "SUBSIDIARY" as const, organization_id: "org", year: 2027, base_revision: "r1", idempotency_key: "packet-one", changes: [], confirmation: { year: 2027, month: 9 }, presentation: { header: { product_name: "Продукт", product_designation: "", product_image: "", factory_name: "" }, actuals: { "2027-09": "0" } } };
  expect(await gateway.saveReportDraft(request)).toEqual(result);
  const query = { report_type: request.report_type, organization_id: request.organization_id, year: request.year, confirmation: request.confirmation, idempotency_key: request.idempotency_key };
  expect(await gateway.getReportDraftSaveResult(query)).toEqual(result);
  expect(save).toHaveBeenCalledWith({ ...request, authorization: { signer_id: "reviewer", pin: "fresh-1" } }); expect(recover).toHaveBeenCalledWith({ ...query, authorization: { signer_id: "reviewer", pin: "fresh-2" } });
  expect(auth.mock.calls.map(([prompt]) => prompt.adminOnly)).toEqual([false, false]);
});
it.each(["transport", "commit_ack", "malformed"])("reports uncertain draft outcome for %s without claiming rollback", async kind => {
  const save = kind === "transport" ? vi.fn().mockRejectedValue(new Error("lost")) : kind === "commit_ack" ? vi.fn().mockResolvedValue({ ok: false, error: { code: "SAVE_OUTCOME_UNKNOWN", message: "unknown" } }) : vi.fn().mockResolvedValue({ ok: true, data: {} });
  const gateway = new PyWebViewGateway({ save_report_draft: save } as never); gateway.setAuthorizationHandler(async (_prompt, execute) => execute({ signer_id: "reviewer", pin: "fresh-code" }));
  await expect(gateway.saveReportDraft({ report_type: "SUBSIDIARY", organization_id: "org", year: 2027, base_revision: "r1", idempotency_key: "one", changes: [], confirmation: { year: 2027, month: 9 } })).rejects.toMatchObject({ outcomeUnknown: true });
});
it("uses an administrator code to create blank reports without changing authorization roles", async () => {
  const create = vi.fn(async () => ({ ok: true, data: { created: true, initial_period: "2027-09-01", organizations: [] } })); const gateway = new PyWebViewGateway({ create_blank_report_profiles: create } as never);
  const authorize = vi.fn<AuthorizationHandler>(async (_prompt, execute) => execute({ signer_id: "admin", pin: "fresh-code" })); gateway.setAuthorizationHandler(authorize);
  await gateway.createBlankReportProfiles({ profile_set_id: "profiles-v1" }); expect(authorize).toHaveBeenCalledWith(expect.objectContaining({ adminOnly: true }), expect.any(Function)); expect(create).toHaveBeenCalledWith({ profile_set_id: "profiles-v1", authorization: { signer_id: "admin", pin: "fresh-code" } });
});
it.each([undefined, null, {}, { ok: false, error: null }, { ok: true }])("classifies an indeterminate outer envelope as uncertain: %j", async envelope => {
  const save = vi.fn().mockResolvedValue(envelope); const gateway = new PyWebViewGateway({ save_report_draft: save } as never);
  gateway.setAuthorizationHandler(async (_prompt, execute) => execute({ signer_id: "reviewer", pin: "fresh-code" }));
  await expect(gateway.saveReportDraft({ report_type: "SUBSIDIARY", organization_id: "org", year: 2027, base_revision: "r1", idempotency_key: "one", changes: [], confirmation: { year: 2027, month: 9 } })).rejects.toMatchObject({ outcomeUnknown: true });
});
