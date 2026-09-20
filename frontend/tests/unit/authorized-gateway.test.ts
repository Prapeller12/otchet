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
