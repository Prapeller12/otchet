import { useCallback, useEffect, useState } from "react";

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

  const activeOrganization = organizations.find((item) => item.id === organizationId);

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="app-eyebrow">Локальный контур</p>
          <h1>Производственная отчётность</h1>
        </div>
        <button className="header-settings-button" type="button" onClick={() => setSettingsOpen(true)}>
          Настроить рабочее поле
        </button>
      </header>

      <div className="workspace-navigation">
        <nav className="report-tabs" aria-label="Формы отчётности">
          {REPORT_TYPES.map((type) => (
            <button
              className={type === reportType ? "report-tab is-active" : "report-tab"}
              key={type}
              type="button"
              aria-current={type === reportType ? "page" : undefined}
              onClick={() => setReportType(type)}
            >
              {REPORT_LABELS[type]}
            </button>
          ))}
        </nav>
        <label className="organization-switcher">
          <span>Организация</span>
          <select value={organizationId} onChange={(event) => setOrganizationId(event.target.value)}>
            {organizations.map((organization) => (
              <option key={organization.id} value={organization.id}>
                {organization.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <main>
        {loadError !== null ? (
          <section className="load-state load-state-error" role="alert">{loadError}</section>
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
