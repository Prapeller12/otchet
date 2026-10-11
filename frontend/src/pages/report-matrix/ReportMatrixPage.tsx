import { useCallback, useEffect, useRef, useState } from "react";

import { useApplicationGateway } from "../../app/providers/ApplicationGatewayProvider";
import type { ReportMatrixContract, DraftNavigation, SnapshotReload } from "../../shared/api/application-gateway";
import type { ReportType } from "../../shared/api/report-cell-contract";
import { ReportMatrix } from "../../widgets/report-matrix/ReportMatrix";

type ReportMatrixPageProps = {
  workspaceMode?: "entry" | "report-settings" | "admin";
  reportType: ReportType;
  organizationId: string;
  reloadKey: number;
  onStatusChange(status: string): void;
  onNavigationBlockedChange?(blocked: boolean): void;
  onNavigationReady?(navigation: DraftNavigation | null): void;
  startPeriod?: string; initialPeriod?: { year: number; month: number };
  onSnapshotReloadReady?(reload: SnapshotReload | null): void;
  externalBusy?: boolean;
};

export function ReportMatrixPage({
  workspaceMode = "entry",
  reportType,
  organizationId,
  reloadKey,
  onStatusChange,
  onNavigationBlockedChange, onNavigationReady, startPeriod, initialPeriod,
  onSnapshotReloadReady, externalBusy = false,
}: ReportMatrixPageProps) {
  const gateway = useApplicationGateway();
  const [matrix, setMatrix] = useState<ReportMatrixContract | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [snapshotKey, setSnapshotKey] = useState(0);
  const navigationBlocked = useRef(false);
  const [year, setYear] = useState(initialPeriod?.year ?? (startPeriod ? Number(startPeriod.slice(0, 4)) : new Date().getFullYear()));
  const navigation = useRef<DraftNavigation | null>(null);
  const handleNavigationReady = useCallback((value: DraftNavigation | null) => { navigation.current = value; onNavigationReady?.(value); }, [onNavigationReady]);
  useEffect(() => { setYear(initialPeriod?.year ?? (startPeriod ? Number(startPeriod.slice(0, 4)) : new Date().getFullYear())); }, [organizationId, startPeriod, initialPeriod?.year]);
  const handleNavigationBlocked = useCallback((blocked: boolean) => {
    // The ref is checked before both loading and adopting a replacement.
    navigationBlocked.current = blocked;
    onNavigationBlockedChange?.(blocked);
  }, [onNavigationBlockedChange]);
  useEffect(() => {
    let active = true;
    onSnapshotReloadReady?.({ prepare: async () => {
      if (navigationBlocked.current) throw new Error("Обновление заблокировано открытым черновиком.");
      const next = await gateway.getReportMatrix({ report_type: reportType, organization_id: organizationId, year });
      if (!active || navigationBlocked.current) throw new Error("Контекст отчёта изменился. Открытая форма сохранена.");
      return () => {
        if (!active || navigationBlocked.current) throw new Error("Обновление заблокировано открытым черновиком.");
        setMatrix(next); setError(null); setSnapshotKey(value => value + 1);
      };
    } });
    return () => { active = false; onSnapshotReloadReady?.(null); };
  }, [gateway, reportType, organizationId, year, onSnapshotReloadReady]);

  useEffect(() => {
    let active = true;
    setMatrix(null);
    setError(null);

    void gateway
      .getReportMatrix({
        report_type: reportType,
        organization_id: organizationId,
        year,
      })
      .then((result) => {
        if (active) setMatrix(result);
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : "Не удалось загрузить форму");
        }
      });

    return () => {
      active = false;
    };
  }, [gateway, organizationId, reloadKey, reportType, year]);

  return <section className="report-page" aria-label="Рабочая форма отчёта">
    <div className="month-controls" aria-label="Период отчёта">
      <label>Год отчёта <select value={year} disabled={matrix === null && error === null} onChange={event => { const nextYear = Number(event.target.value); const action = () => setYear(nextYear); if (navigation.current) navigation.current.request(action); else action(); }}>
        {Array.from({ length: 201 }, (_, index) => 1900 + index).filter(value => !startPeriod || value >= Number(startPeriod.slice(0, 4))).map(value => <option key={value} value={value}>{value}</option>)}
      </select></label>
    </div>
    {error !== null ? (
      <section className="load-state load-state-error" role="alert">
        <strong>Форма не загружена</strong>
        <span>{error}</span>
      </section>
    ) : matrix === null ? (
      <section className="load-state" aria-live="polite">Загрузка матрицы…</section>
    ) : (
      <ReportMatrix
        key={`${matrix.report_type}:${matrix.organization_id}:${reloadKey}:${year}:${snapshotKey}`}
        gateway={gateway}
        workspaceMode={workspaceMode}
        matrix={matrix}
        onChange={setMatrix}
        onStatusChange={onStatusChange}
        onNavigationBlockedChange={handleNavigationBlocked}
        onNavigationReady={handleNavigationReady}
        externalBusy={externalBusy}
        {...(startPeriod ? { startPeriod } : {})}
        {...(initialPeriod?.year === year ? { initialMonth: initialPeriod.month } : startPeriod && Number(startPeriod.slice(0, 4)) === year ? { initialMonth: Number(startPeriod.slice(5, 7)) } : {})}
      />
    )}
  </section>;
}
