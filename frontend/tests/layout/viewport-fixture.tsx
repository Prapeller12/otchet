import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "../../src/app/App";
import { ApplicationGatewayProvider } from "../../src/app/providers/ApplicationGatewayProvider";
import { DemoGateway, createDemoMatrix } from "../../src/shared/api/demo-gateway";
import type { AuthorizationHandler, ReportMatrixContract, ReportMatrixQuery, SaveReportDraftRequest } from "../../src/shared/api/application-gateway";
import { sourceMatrix } from "../fixtures/source-matrix";
import { draftResult } from "../fixtures/report-draft";
import "../../src/styles/global.css";
import "../../src/styles/design-tokens.css";
import "../../src/styles/legacy-print.css";
import "../../src/assets/fonts/golos-text.css";
import "../../src/styles/reference-tokens.css";
import "../../src/styles/reference-theme.css";

/** Synthetic geometry fixture only: no source books, real names, database or writes. */
function matrixFor(query: ReportMatrixQuery): ReportMatrixContract {
  const year = query.year ?? 2027;
  const base = query.report_type === "DAILY_MOVEMENT" ? createDemoMatrix(query.report_type) : sourceMatrix();
  base.organization_id = query.organization_id; base.report_type = query.report_type; base.year = year;
  base.head_site = query.report_type === "HEAD_SITE"; base.title = query.report_type === "HEAD_SITE" ? "Головная площадка" : query.report_type === "SUBSIDIARY" ? "Дочерние общества" : base.title;
  base.time_columns = query.report_type === "DAILY_MOVEMENT"
    ? Array.from({ length: 30 }, (_, index) => ({ id: `${year}-09-${String(index + 1).padStart(2, "0")}`, group_label: `${year}-09`, label: `${index + 1}.09`, width: 92 }))
    : [...["OPENING", "RECEIVED", "STOCK", "VARIANCE", "USED"].map(kind => ({ id: `${year}-09-${kind}`, kind, label: kind, group_label: `${year}-09`, width: 110 })),
      ...Array.from({ length: 12 }, (_, index) => `${year}-${String(index + 1).padStart(2, "0")}`).flatMap(month => (base.head_site ? ["PLAN", "FACT"] : ["SUPPLIED", "USED"]).map(kind => ({ id: `${month}-01-${kind}`, kind, label: kind === "PLAN" ? "План" : kind === "FACT" ? "Факт" : "01–07", group_label: month, width: 100 })))];
  const original = base.rows;
  base.rows = Array.from({ length: 80 }, (_, index) => {
    const row = structuredClone(original[index % original.length]!);
    row.id = `layout-row-${index}`; row.group_id = `layout-group-${index}`;
    row.cells = base.time_columns.map(column => ({ ...structuredClone(row.cells[0]!), column_id: column.id,
      coordinate: { report_type: query.report_type, organization_id: query.organization_id, component_id: `layout-component-${index}`, metric_code: column.id, period_start: `${column.group_label}-01` },
      value: { kind: "QUANTITY", quantity: String(index + 1) }, state: { access: column.kind === "STOCK" || column.kind === "VARIANCE" ? "calculated" : "editable", persistence: "saved" },
    }));
    return row;
  });
  base.capabilities.import = { enabled: true }; base.capabilities.export = { enabled: true };
  base.presentation = { ...base.presentation, production_codes: Array.from({ length: 8 }, (_, index) => ({ id: `layout-code-${index}`, label: `Код ${index + 1}`, plans: {}, actuals: {} })) };
  const widthStress = new URLSearchParams(location.search).get("wide");
  if (widthStress) base.presentation.widths = widthStress === "photo" ? { photo: 600, number: 600 } : { position: 600 };
  return base;
}
let authorize: AuthorizationHandler | null = null;
const gateway = Object.assign(new DemoGateway(), {
  setAuthorizationHandler: (handler: AuthorizationHandler | null) => { authorize = handler; },
  listOrganizations: async () => ({ organizations: [{ id: "head", name: "Головная площадка", kind: "HEAD" as const, start_period: "2027-09-01" }, { id: "sub", name: "Дочернее общество", kind: "SUBSIDIARY" as const, start_period: "2027-09-01" }] }),
  getReportMatrix: async (query: ReportMatrixQuery) => matrixFor(query),
  listReportSigners: async () => [{ id: "layout-admin", display_name: "Администратор для проверки геометрии", role: "admin" as const, key_fingerprint: "synthetic", created_at: "2027-09-01" }],
  saveReportDraft: async (request: SaveReportDraftRequest) => {
    if (authorize) await authorize({ title: "Сохранить и подтвердить отчёт", adminOnly: false }, async () => null);
    return draftResult(matrixFor(request), request);
  },
  exportPdf: async () => {
    if (authorize) await authorize({ title: "Создать PDF", adminOnly: false }, async () => null);
    return { cancelled: true };
  },
  getReportVerification: async () => ({ status: "UNVERIFIED" as const, snapshot_sha256: "synthetic" }),
  validateImport: async () => ({ cancelled: false, batch_id: "layout-import", file_name: "synthetic.xlsx", status: "INVALID" as const, error_count: 50, issues: Array.from({ length: 50 }, (_, index) => ({ row_number: index + 1, code: "LAYOUT", message: `Проверьте строку ${index + 1}: длинное описание проблемы для проверки переноса и прокрутки.` })) }),
  listBlankReportProfiles: async () => ({ profile_set_id: "synthetic-layout", initial_period: "2027-09-01", quantity_policy: "DATA_NOT_PROVIDED", organizations: Array.from({ length: 5 }, (_, index) => ({ template_id: `layout-${index}`, label: `Площадка ${index + 1}`, report_type: index ? "SUBSIDIARY" as const : "HEAD_SITE" as const, position_count: 80, distinct_manufacturer_count: 8, supplier_row_count: 80, display_row_count: 80 })) }),
  createBlankReportProfiles: async () => ({ created: false, initial_period: "2027-09-01", organizations: [{ template_id: "layout-0", label: "Площадка 1", organization_id: "head", report_type: "HEAD_SITE" as const }] }),
});
const accessFixture = new URLSearchParams(location.search).get("access");
if (accessFixture) Object.assign(gateway, {
  getAccessStatus: async () => accessFixture === "setup"
    ? { state: "setup", users: [] }
    : { state: "ready", users: [], automatic_open_error: "Синтетическая проверка уведомления открытия: повторите код при следующем запуске." },
  listRecoveryBackups: async () => ({ backups: [] }),
});
if (new URLSearchParams(location.search).has("network")) Object.assign(gateway, {
  getAccessStatus: async () => ({ state: "ready", users: [] }),
  getNetworkStatus: async () => ({ mode: "network", state: "ready", loaded_global_revision: "synthetic:g1", current_global_revision: "synthetic:g1", snapshot_changed: false, can_write: true, message: null, pending_operation_id: null }),
  refreshNetworkData: async () => { throw new Error("Синтетическая проверка сетевой ошибки: общая папка временно недоступна; открытая таблица и введённые значения сохраняются. ".repeat(8)); },
  getNetworkOperationResult: async (operationId: string) => ({ found: false, operation_id: operationId, data_state: "unknown", result: null, global_revision: null, current_global_revision: "synthetic:g1" }),
});
localStorage.setItem("reporting-onboarding-v1", "done");
createRoot(document.getElementById("root")!).render(<StrictMode><ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider></StrictMode>);
