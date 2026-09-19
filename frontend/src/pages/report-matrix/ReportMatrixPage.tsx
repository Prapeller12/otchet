import { useCallback, useEffect, useState } from "react";

import { useApplicationGateway } from "../../app/providers/ApplicationGatewayProvider";
import type { ReportMatrixContract } from "../../shared/api/application-gateway";
import type { ReportType } from "../../shared/api/report-cell-contract";
import { ReportMatrix } from "../../widgets/report-matrix/ReportMatrix";

type ReportMatrixPageProps = {
  reportType: ReportType;
  organizationId: string;
  reloadKey: number;
  onStatusChange(status: string): void;
  onNavigationBlockedChange?(blocked: boolean): void;
};

export function ReportMatrixPage({
  reportType,
  organizationId,
  reloadKey,
  onStatusChange,
  onNavigationBlockedChange,
}: ReportMatrixPageProps) {
  const gateway = useApplicationGateway();
  const [matrix, setMatrix] = useState<ReportMatrixContract | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [year, setYear] = useState(new Date().getFullYear());
  const [navigationBlocked, setNavigationBlocked] = useState(false);
  const handleNavigationBlocked = useCallback((blocked: boolean) => {
    setNavigationBlocked(blocked);
    onNavigationBlockedChange?.(blocked);
  }, [onNavigationBlockedChange]);

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

  return <>
    <div className="month-controls" aria-label="Период отчёта">
      <label>Год отчёта <select value={year} disabled={navigationBlocked || (matrix === null && error === null)} onChange={event => setYear(Number(event.target.value))}>
        {Array.from({ length: 201 }, (_, index) => 1900 + index).map(value => <option key={value} value={value}>{value}</option>)}
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
        key={`${matrix.report_type}:${matrix.organization_id}:${reloadKey}:${year}`}
        gateway={gateway}
        matrix={matrix}
        onChange={setMatrix}
        onStatusChange={onStatusChange}
        onNavigationBlockedChange={handleNavigationBlocked}
      />
    )}
  </>;
}
