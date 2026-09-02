import { useEffect, useMemo, useState } from "react";

import type {
  ApplicationGateway,
  OrganizationOption,
  ReportLayoutContract,
  ReportLayoutRow,
} from "../../shared/api/application-gateway";
import type { ReportType } from "../../shared/api/report-cell-contract";

const REPORT_LABELS: Record<ReportType, string> = {
  DAILY_MOVEMENT: "Ежедневный отчёт",
  HEAD_SITE: "Головная площадка",
  SUBSIDIARY: "Дочерние общества",
};

type WorkspaceSettingsDialogProps = {
  gateway: ApplicationGateway;
  organizations: OrganizationOption[];
  initialOrganizationId: string;
  initialReportType: ReportType;
  onOrganizationsChange(
    organizations: OrganizationOption[],
    selectedOrganizationId: string,
  ): void;
  onApply(reportType: ReportType, organizationId: string): void;
  onClose(): void;
};

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Операция не выполнена";
}

function newRow(layout: ReportLayoutContract): ReportLayoutRow | null {
  const template = layout.templates[0];
  if (template === undefined) return null;
  return {
    id: null,
    template_group_id: template.id,
    party_name: "Изготовитель/поставщик",
    position_name: template.label,
  };
}

export function WorkspaceSettingsDialog({
  gateway,
  organizations,
  initialOrganizationId,
  initialReportType,
  onOrganizationsChange,
  onApply,
  onClose,
}: WorkspaceSettingsDialogProps) {
  const [organizationId, setOrganizationId] = useState(initialOrganizationId);
  const [reportType, setReportType] = useState(initialReportType);
  const [layout, setLayout] = useState<ReportLayoutContract | null>(null);
  const [rows, setRows] = useState<ReportLayoutRow[]>([]);
  const [organizationName, setOrganizationName] = useState("");
  const [newOrganizationName, setNewOrganizationName] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedOrganization = useMemo(
    () => organizations.find((item) => item.id === organizationId),
    [organizationId, organizations],
  );

  useEffect(() => {
    setOrganizationName(selectedOrganization?.name ?? "");
  }, [selectedOrganization]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void gateway
      .getReportLayout({ report_type: reportType, organization_id: organizationId })
      .then((result) => {
        if (!active) return;
        setLayout(result);
        setRows(result.rows);
      })
      .catch((reason: unknown) => {
        if (active) setError(errorMessage(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [gateway, organizationId, reportType]);

  function updateRow(index: number, update: Partial<ReportLayoutRow>): void {
    setRows((current) =>
      current.map((row, rowIndex) =>
        rowIndex === index ? { ...row, ...update } : row,
      ),
    );
  }

  function moveRow(index: number, direction: -1 | 1): void {
    setRows((current) => {
      const target = index + direction;
      if (target < 0 || target >= current.length) return current;
      const next = [...current];
      const [item] = next.splice(index, 1);
      if (item !== undefined) next.splice(target, 0, item);
      return next;
    });
  }

  async function addOrganization(): Promise<void> {
    if (!newOrganizationName.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const result = await gateway.createOrganization(newOrganizationName);
      const next = [...organizations, result.organization];
      setNewOrganizationName("");
      setOrganizationId(result.organization.id);
      onOrganizationsChange(next, result.organization.id);
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  async function renameOrganization(): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      const result = await gateway.renameOrganization(organizationId, organizationName);
      const next = organizations.map((item) =>
        item.id === organizationId ? result.organization : item,
      );
      onOrganizationsChange(next, organizationId);
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  async function archiveOrganization(): Promise<void> {
    if (selectedOrganization?.kind !== "SUBSIDIARY") return;
    setSaving(true);
    setError(null);
    try {
      const result = await gateway.archiveOrganization(organizationId);
      const nextId = result.organizations[0]?.id;
      if (nextId === undefined) throw new Error("Не осталось доступных организаций");
      setOrganizationId(nextId);
      onOrganizationsChange(result.organizations, nextId);
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  async function saveLayout(): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      await gateway.saveReportLayout({
        report_type: reportType,
        organization_id: organizationId,
        rows,
      });
      onApply(reportType, organizationId);
      onClose();
    } catch (reason: unknown) {
      setError(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-backdrop" role="presentation">
      <section
        className="settings-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="workspace-settings-title"
      >
        <header className="settings-header">
          <div>
            <p>Настройка рабочего поля</p>
            <h2 id="workspace-settings-title">Организации и строки отчёта</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть">
            ×
          </button>
        </header>

        <div className="settings-body">
          <aside className="organization-settings">
            <label>
              Организация
              <select value={organizationId} onChange={(event) => setOrganizationId(event.target.value)}>
                {organizations.map((organization) => (
                  <option key={organization.id} value={organization.id}>
                    {organization.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Наименование
              <input
                value={organizationName}
                onChange={(event) => setOrganizationName(event.target.value)}
              />
            </label>
            <div className="compact-actions">
              <button className="button secondary" type="button" disabled={saving} onClick={() => void renameOrganization()}>
                Переименовать
              </button>
              {selectedOrganization?.kind === "SUBSIDIARY" && (
                <button className="button danger" type="button" disabled={saving} onClick={() => void archiveOrganization()}>
                  Убрать общество
                </button>
              )}
            </div>
            <div className="new-organization">
              <strong>Новое дочернее общество</strong>
              <input
                value={newOrganizationName}
                placeholder="Введите наименование"
                onChange={(event) => setNewOrganizationName(event.target.value)}
              />
              <button className="button secondary" type="button" disabled={saving || !newOrganizationName.trim()} onClick={() => void addOrganization()}>
                Добавить общество
              </button>
            </div>
          </aside>

          <div className="layout-settings">
            <label className="report-setting-select">
              Настраиваемый отчёт
              <select value={reportType} onChange={(event) => setReportType(event.target.value as ReportType)}>
                {Object.entries(REPORT_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>

            {loading || layout === null ? (
              <div className="settings-empty">Загрузка настроек…</div>
            ) : (
              <>
                <div className="layout-list-header">
                  <strong>Строки рабочего поля</strong>
                  <button
                    className="button secondary"
                    type="button"
                    onClick={() => {
                      const row = newRow(layout);
                      if (row !== null) setRows((current) => [...current, row]);
                    }}
                  >
                    + Добавить строку
                  </button>
                </div>
                <div className="layout-row-list">
                  {rows.length === 0 && (
                    <div className="settings-empty">Добавьте первую строку отчёта.</div>
                  )}
                  {rows.map((row, index) => (
                    <div className="layout-row-editor" key={row.id ?? `new-${index}`}>
                      <select
                        aria-label="Тип строки"
                        value={row.template_group_id}
                        onChange={(event) => updateRow(index, { template_group_id: event.target.value })}
                      >
                        {layout.templates.map((template) => (
                          <option key={template.id} value={template.id}>{template.label}</option>
                        ))}
                      </select>
                      <input
                        aria-label="Изготовитель или поставщик"
                        value={row.party_name}
                        placeholder="Изготовитель/поставщик"
                        onChange={(event) => updateRow(index, { party_name: event.target.value })}
                      />
                      <input
                        aria-label="Позиция"
                        value={row.position_name}
                        placeholder="Позиция"
                        onChange={(event) => updateRow(index, { position_name: event.target.value })}
                      />
                      <div className="row-actions">
                        <button type="button" className="mini-button" onClick={() => moveRow(index, -1)} aria-label="Переместить выше">↑</button>
                        <button type="button" className="mini-button" onClick={() => moveRow(index, 1)} aria-label="Переместить ниже">↓</button>
                        <button type="button" className="mini-button remove" onClick={() => setRows((current) => current.filter((_, rowIndex) => rowIndex !== index))} aria-label="Убрать строку">×</button>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>

        {error !== null && <div className="settings-error" role="alert">{error}</div>}
        <footer className="settings-footer">
          <span>Удалённые строки и общества архивируются; введённые данные сохраняются.</span>
          <div>
            <button className="button secondary" type="button" onClick={onClose}>Отмена</button>
            <button className="button primary" type="button" disabled={saving || loading || layout === null} onClick={() => void saveLayout()}>
              {saving ? "Сохранение…" : "Применить настройки"}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}
