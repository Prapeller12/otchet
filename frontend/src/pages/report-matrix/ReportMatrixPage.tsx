import { useEffect, useState } from "react";

import { useApplicationGateway } from "../../app/providers/ApplicationGatewayProvider";
import type { ReportMatrixContract } from "../../shared/api/application-gateway";
import type { ReportType } from "../../shared/api/report-cell-contract";
import { ReportMatrix } from "../../widgets/report-matrix/ReportMatrix";

type ReportMatrixPageProps = {
  reportType: ReportType;
  organizationId: string;
  reloadKey: number;
  onStatusChange(status: string): void;
};

export function ReportMatrixPage({
  reportType,
  organizationId,
  reloadKey,
  onStatusChange,
}: ReportMatrixPageProps) {
  const gateway = useApplicationGateway();
  const [matrix, setMatrix] = useState<ReportMatrixContract | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setMatrix(null);
    setError(null);

    void gateway
      .getReportMatrix({
        report_type: reportType,
        organization_id: organizationId,
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
  }, [gateway, organizationId, reloadKey, reportType]);

  if (error !== null) {
    return (
      <section className="load-state load-state-error" role="alert">
        <strong>Форма не загружена</strong>
        <span>{error}</span>
      </section>
    );
  }

  if (matrix === null) {
    return (
      <section className="load-state" aria-live="polite">
        Загрузка матрицы…
      </section>
    );
  }

  return (
    <ReportMatrix
      key={`${matrix.report_type}:${matrix.organization_id}:${reloadKey}`}
      gateway={gateway}
      matrix={matrix}
      onChange={setMatrix}
      onStatusChange={onStatusChange}
    />
  );
}
