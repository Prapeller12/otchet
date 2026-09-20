import { ReferenceReport } from "../features/reference-reports/ReferenceReport";
import type { ReferenceSummary } from "../shared/api/application-gateway";
import { useCallback, useEffect, useRef, useState } from "react";
import { AccessGate } from "../features/access/AccessGate";
import { AuthorizationDialog, type PendingAuthorization } from "../features/access/AuthorizationDialog";
import { Onboarding } from "../features/access/Onboarding";
import { ResponsibleUsers } from "../features/access/ResponsibleUsers";
import type { AuthorizationHandler } from "../shared/api/application-gateway";
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
  return <AccessGate gateway={gateway}><Workspace /></AccessGate>;
}

function Workspace() {
  const gateway = useApplicationGateway();
  const [workspaceMode, setWorkspaceMode] = useState<"entry" | "report-settings" | "admin">("entry");
  const [modeError, setModeError] = useState("");
  const [leavingAdministration, setLeavingAdministration] = useState(false);
  const [adminSection, setAdminSection] = useState<"reports" | "users">("reports");
  const [onboardingOpen, setOnboardingOpen] = useState(() => {
    try { return gateway.mode === "pywebview" && localStorage.getItem("reporting-onboarding-v1") !== "done"; } catch { return gateway.mode === "pywebview"; }
  });
  const [pendingAuthorization, setPendingAuthorization] = useState<PendingAuthorization | null>(null);
  const authorizationRef = useRef<PendingAuthorization | null>(null);
  const requestAuthorization = useCallback<AuthorizationHandler>((prompt, execute) => new Promise((resolve, reject) => {
    if (authorizationRef.current) { reject(new Error("Сначала завершите открытое подтверждение.")); return; }
    const pending = { ...prompt, execute, resolve, reject };
    authorizationRef.current = pending;
    setPendingAuthorization(pending);
  }), []);
  useEffect(() => {
    gateway.setAuthorizationHandler?.(requestAuthorization);
    return () => {
      gateway.setAuthorizationHandler?.(null);
      authorizationRef.current?.reject(new Error("Подтверждение закрыто."));
      authorizationRef.current = null;
    };
  }, [gateway, requestAuthorization]);
  function closeAuthorization() { authorizationRef.current = null; setPendingAuthorization(null); }
  function closeOnboarding() {
    setOnboardingOpen(false);
    try { localStorage.setItem("reporting-onboarding-v1", "done"); } catch { /* Optional local preference. */ }
  }
  async function leaveEditing() {
    setModeError("");
    setLeavingAdministration(true);
    try {
      if (workspaceMode === "admin") await gateway.endAdministration?.();
      setWorkspaceMode("entry"); setAdminSection("reports");
    } catch (reason) {
      setModeError(reason instanceof Error ? reason.message : "Не удалось закрыть раздел администратора. Повторите выход.");
    } finally { setLeavingAdministration(false); }
  }
  async function openAdministration() {
    setModeError("");
    if (gateway.mode === "demo") { setWorkspaceMode("admin"); return; }
    try {
      await requestAuthorization({ title: "Открыть раздел администратора", adminOnly: true }, async authorization => {
        const user = await gateway.authenticateAccess!(authorization);
        if (user.role !== "admin") throw new Error("Требуется код администратора.");
        return user;
      });
      setWorkspaceMode("admin"); setAdminSection("reports");
    } catch { /* Cancel preserves the current workspace. */ }
  }
  const [reportType, setReportType] = useState<ReportType>("DAILY_MOVEMENT");
  const [organizations, setOrganizations] = useState<OrganizationOption[]>([]);
  const [organizationId, setOrganizationId] = useState("");
  const [referenceId, setReferenceId] = useState("");
  const [references, setReferences] = useState<ReferenceSummary[]>([]);
  const [referenceDirty, setReferenceDirty] = useState(false);
  const [matrixBlocked, setMatrixBlocked] = useState(false);
  const navigationBlocked = referenceDirty || matrixBlocked;
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

  useEffect(() => {
    if (!navigationBlocked) return;
    const warnBeforeClose = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeClose);
    return () => window.removeEventListener("beforeunload", warnBeforeClose);
  }, [navigationBlocked]);

  const activeOrganization = organizations.find((item) => item.id === organizationId);
  const activeReferences = references.filter((report) => report.report_type === reportType);

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-brand">
          <p className="app-eyebrow">{workspaceMode === "admin" ? "Раздел администратора" : workspaceMode === "report-settings" ? "План и сведения" : "Заполнение отчётов"}</p>
          <h1>Производственная отчётность</h1>
        </div>
        <div className="workspace-mode-actions">
          <button type="button" disabled={navigationBlocked} onClick={() => setOnboardingOpen(true)}>Как заполнить</button>
          {workspaceMode === "entry" ? <>
            <button type="button" disabled={navigationBlocked || !!referenceId} onClick={() => setWorkspaceMode("report-settings")}>План и сведения</button>
            <button type="button" disabled={navigationBlocked} onClick={() => void openAdministration()}>Администратор</button>
          </> : <button type="button" disabled={navigationBlocked || leavingAdministration} onClick={() => void leaveEditing()}>К заполнению отчётов</button>}
        </div>
      </header>
      {workspaceMode === "admin" && <div className="admin-navigation" aria-label="Разделы администратора">
        <p>Настройте формы и выдайте личные ключи.</p>
        <button className="button secondary" disabled={navigationBlocked} aria-pressed={adminSection === "reports"} onClick={() => setAdminSection("reports")}>План и сведения</button>
        <button className="button secondary" disabled={navigationBlocked || !!referenceId} onClick={() => setSettingsOpen(true)}>Настроить рабочее поле</button>
        <button className="button secondary" disabled={navigationBlocked} aria-pressed={adminSection === "users"} onClick={() => setAdminSection("users")}>Ответственные лица</button>
      </div>}

      <div className="workspace-navigation">
        <nav className="report-tabs" aria-label="Формы отчётности">
          {REPORT_TYPES.map((type) => (
            <button
              className={type === reportType ? "report-tab is-active" : "report-tab"}
              key={type}
              type="button"
              aria-current={type === reportType ? "page" : undefined}
              disabled={navigationBlocked}
              onClick={() => { setReferenceId(""); setReportType(type); }}
            >
              <UiIcon name={type === "DAILY_MOVEMENT" ? "calendar" : type === "HEAD_SITE" ? "factory" : "buildings"} />
              {REPORT_LABELS[type]}
            </button>
          ))}
        </nav>
        <label className="organization-switcher">
          <span>Организация</span>
          <select disabled={navigationBlocked} value={organizationId} onChange={(event) => setOrganizationId(event.target.value)}>
            {organizations.map((organization) => (
              <option key={organization.id} value={organization.id}>
                {organization.name}
              </option>
            ))}
          </select>
        </label>

      </div>

      {activeReferences.length > 0 && <label className="reference-selector">Сохранённые отчёты Excel
        <select aria-label="Сохранённые отчёты Excel" disabled={navigationBlocked} value={referenceId} onChange={e => setReferenceId(e.target.value)}>
          <option value="">Рабочая форма</option>
          {activeReferences.map(r => <option key={r.id} value={r.id}>{r.file_name}</option>)}
        </select>
      </label>}
      {matrixBlocked && <p className="workspace-edit-notice" role="status">Завершите ввод и сохраните изменения перед переходом в другую форму, организацию или настройки.</p>}
      {modeError && <p className="workspace-edit-notice" role="alert">{modeError}</p>}
      <main>
        {workspaceMode === "admin" && adminSection === "users" ? <ResponsibleUsers gateway={gateway} /> : loadError !== null ? (
          <section className="load-state load-state-error" role="alert">{loadError}</section>
        ) : referenceId ? (
          <ReferenceReport gateway={gateway} organizationId={organizationId} identity={referenceId} onBack={() => setReferenceId("")} onDirty={setReferenceDirty} />
        ) : organizationId ? (
          <ReportMatrixPage
            workspaceMode={workspaceMode}
            reportType={reportType}
            organizationId={organizationId}
            reloadKey={reloadKey}
            onStatusChange={handleStatusChange}
            onNavigationBlockedChange={setMatrixBlocked}
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
          {gateway.mode === "pywebview" ? "Данные на этом компьютере" : "Демо без записи на диск"}
        </span>
      </footer>

      {onboardingOpen && <Onboarding onClose={closeOnboarding} />}
      {pendingAuthorization && <AuthorizationDialog gateway={gateway} pending={pendingAuthorization} onClose={closeAuthorization} />}
      {workspaceMode === "admin" && settingsOpen && organizationId && (
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
