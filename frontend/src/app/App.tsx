import { ReferenceReport } from "../features/reference-reports/ReferenceReport";
import type { ReferenceSummary } from "../shared/api/application-gateway";
import { useCallback, useEffect, useState } from "react";
import { UiIcon } from "../shared/ui/UiIcon";

import { WorkspaceSettingsDialog } from "../features/workspace-settings/WorkspaceSettingsDialog";
import { ReportMatrixPage } from "../pages/report-matrix/ReportMatrixPage";
import { useApplicationGateway } from "./providers/ApplicationGatewayProvider";
import type { OrganizationOption } from "../shared/api/application-gateway";
import {
  REPORT_TYPES,
  type ReportType,
} from "../shared/api/report-cell-contract";

const REPORT_LABELS: Record<ReportType, string> = {
  DAILY_MOVEMENT: "Ежедневный отчёт",
  HEAD_SITE: "Головная площадка",
  SUBSIDIARY: "Дочерние общества",
};

export function App() {
  const gateway = useApplicationGateway();
  const [reportType, setReportType] = useState<ReportType>("DAILY_MOVEMENT");
  const [organizations, setOrganizations] = useState<OrganizationOption[]>([]);
  const [organizationId, setOrganizationId] = useState("");
  const [referenceId, setReferenceId] = useState("");
  const [references, setReferences] = useState<ReferenceSummary[]>([]);
  const [referenceDirty, setReferenceDirty] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [cellStatus, setCellStatus] = useState("Готово");
  const [loadError, setLoadError] = useState<string | null>(null);

  const handleStatusChange = useCallback((status: string) => {
    setCellStatus(status);
  }, []);

  useEffect(() => {
    let active = true;
    void gateway
      .listOrganizations()
      .then((result) => {
        if (!active) return;
        setOrganizations(result.organizations);
        setOrganizationId((current) => current || result.organizations[0]?.id || "");
      })
      .catch((reason: unknown) => {
        if (active) {
          setLoadError(
            reason instanceof Error ? reason.message : "Не удалось загрузить организации",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [gateway]);

  useEffect(() => {
    let active = true;
    setReferenceId("");
    setReferences([]);
    const refresh = (identity?: string) => {
      if (!organizationId || !gateway.referenceReport) return;
      void gateway.referenceReport({ action: "list", organization_id: organizationId })
        .then(result => { if (active) { setReferences(result as ReferenceSummary[]); if (identity) { setReferenceId(identity); const found = (result as ReferenceSummary[]).find(r => r.id === identity); if (found) setReportType(found.report_type); } } })
        .catch(e => { if (active) setLoadError(String(e)); });
    };
    const imported = (event: Event) => refresh((event as CustomEvent<{ id: string }>).detail.id);
    refresh();
    window.addEventListener("reference-report-imported", imported);
    return () => { active = false; window.removeEventListener("reference-report-imported", imported); };
  }, [gateway, organizationId]);

  const activeOrganization = organizations.find((item) => item.id === organizationId);
  const activeReferences = references.filter((report) => report.report_type === reportType);

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-brand">
          <p className="app-eyebrow">Локальный контур</p>
          <h1>Производственная отчётность</h1>
        </div>
      </header>

      <div className="workspace-navigation">
        <nav className="report-tabs" aria-label="Формы отчётности">
          {REPORT_TYPES.map((type) => (
            <button
              className={type === reportType ? "report-tab is-active" : "report-tab"}
              key={type}
              type="button"
              aria-current={type === reportType ? "page" : undefined}
              disabled={referenceDirty}
              onClick={() => { setReferenceId(""); setReportType(type); }}
            >
              <UiIcon name={type === "DAILY_MOVEMENT" ? "calendar" : type === "HEAD_SITE" ? "factory" : "buildings"} />
              {REPORT_LABELS[type]}
            </button>
          ))}
        </nav>
        <label className="organization-switcher">
          <span>Организация</span>
          <select disabled={referenceDirty} value={organizationId} onChange={(event) => setOrganizationId(event.target.value)}>
            {organizations.map((organization) => (
              <option key={organization.id} value={organization.id}>
                {organization.name}
              </option>
            ))}
          </select>
        </label>
        <button className="header-settings-button" type="button" disabled={referenceDirty || !!referenceId} onClick={() => setSettingsOpen(true)}>
          <UiIcon name="settings" />
          Настроить рабочее поле
        </button>
      </div>

      {activeReferences.length > 0 && <label className="reference-selector">Сохранённые отчёты Excel
        <select aria-label="Сохранённые отчёты Excel" disabled={referenceDirty} value={referenceId} onChange={e => setReferenceId(e.target.value)}>
          <option value="">Рабочая форма</option>
          {activeReferences.map(r => <option key={r.id} value={r.id}>{r.file_name}</option>)}
        </select>
      </label>}
      <main>
        {loadError !== null ? (
          <section className="load-state load-state-error" role="alert">{loadError}</section>
        ) : referenceId ? (
          <ReferenceReport gateway={gateway} organizationId={organizationId} identity={referenceId} onBack={() => setReferenceId("")} onDirty={setReferenceDirty} />
        ) : organizationId ? (
          <ReportMatrixPage
            reportType={reportType}
            organizationId={organizationId}
            reloadKey={reloadKey}
            onStatusChange={handleStatusChange}
          />
        ) : (
          <section className="load-state">Загрузка организаций…</section>
        )}
      </main>

      <footer className="application-status-bar" aria-label="Состояние рабочего поля">
        <span className="status-ready">Готово</span>
        <span className="status-cell">{cellStatus}</span>
        <span className="status-context">
          {activeOrganization?.name ?? "Организация не выбрана"} · {REPORT_LABELS[reportType]}
        </span>
        <span className="status-database">
          <i aria-hidden="true" />
          {gateway.mode === "pywebview" ? "SQLite подключена" : "Демо без записи на диск"}
        </span>
      </footer>

      {settingsOpen && organizationId && (
        <WorkspaceSettingsDialog
          gateway={gateway}
          organizations={organizations}
          initialOrganizationId={organizationId}
          initialReportType={reportType}
          onOrganizationsChange={(next, selected) => {
            setOrganizations(next);
            setOrganizationId(selected);
          }}
          onApply={(nextReportType, nextOrganizationId) => {
            setReportType(nextReportType);
            setOrganizationId(nextOrganizationId);
            setReloadKey((current) => current + 1);
          }}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </div>
  );
}
