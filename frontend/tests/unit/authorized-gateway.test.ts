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

it("requires administrator confirmation for configuration, plans, organizations and workbook changes", async () => {
  const success = vi.fn(async () => ({ ok: true, data: {} }));
  const gateway = new PyWebViewGateway({ save_report_presentation: success, create_organization: success, save_report_layout: success, reference_report: success } as never);
  const authorize = vi.fn<AuthorizationHandler>(async (_prompt, execute) => execute({ signer_id: "admin", pin: "secret" }));
  gateway.setAuthorizationHandler(authorize);
  await gateway.createOrganization("Завод");
  await gateway.saveReportLayout({ report_type: "HEAD_SITE", organization_id: "org", rows: [] });
  await gateway.saveReportPresentation({ report_type: "HEAD_SITE", organization_id: "org", plans: { "2026-01": "10" } });
  await gateway.referenceReport({ action: "save", organization_id: "org", id: "book", changes: [] });
  expect(authorize).toHaveBeenCalledTimes(4);
  for (const [prompt] of authorize.mock.calls) expect(prompt.adminOnly).toBe(true);
});

it("uses the signature code for write authorization without requesting a second code", async () => {
  const verify = vi.fn(async () => ({ ok: true, data: { status: "VERIFIED" } }));
  const gateway = new PyWebViewGateway({ verify_report: verify } as never);
  const request = { report_type: "HEAD_SITE" as const, organization_id: "org", month: 1, signer_id: "reviewer", pin: "secret", snapshot_sha256: "hash", confirmed: true };
  await gateway.verifyReport(request);
  expect(verify).toHaveBeenCalledWith({ ...request, authorization: { signer_id: "reviewer", pin: "secret" } });
});

it("requires fresh administrator authorization when archiving an organization or saving plan cells", async () => {
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
  for (const [prompt] of authorize.mock.calls) expect(prompt.adminOnly).toBe(true);
  expect(archive).toHaveBeenCalledWith({ organization_id: "org", authorization: { signer_id: "admin", pin: "secret" } });
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ authorization: { signer_id: "admin", pin: "secret" } }));
});
