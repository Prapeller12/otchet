import type { ReportMatrixContract, SaveReportDraftRequest, SaveReportDraftResponse } from "../../src/shared/api/application-gateway";
export function draftResult(matrix: ReportMatrixContract, request?: SaveReportDraftRequest, overrides: Partial<SaveReportDraftResponse> = {}): SaveReportDraftResponse {
  const patch = request?.presentation ?? {};
  const { production_code_actuals, production_code_plans, ...presentation } = patch;
  const saved = { ...matrix.presentation, ...presentation };
  if (saved.production_codes) saved.production_codes = saved.production_codes.map(code => ({ ...code, actuals: { ...code.actuals, ...production_code_actuals?.[code.id] }, plans: { ...code.plans, ...production_code_plans?.[code.id] } }));
  return { matrix_revision: "r2", operation_revision: "r2", current_revision: "r2", data_state: "committed", idempotent_replay: false, cells: (request?.changes ?? []).map(change => ({ ...change, state: { access: "editable", persistence: "saved" } })), presentation: saved, verification_state: "verified", backup_state: "complete", ...overrides };
}
