import { useEffect, useState } from "react";

import { useApplicationGateway } from "../../app/providers/ApplicationGatewayProvider";
import type { ReportMatrixContract } from "../../shared/api/application-gateway";
import type { ReportType } from "../../shared/api/report-cell-contract";
import { ReportMatrix } from "../../widgets/report-matrix/ReportMatrix";

type ReportMatrixPageProps = {
  reportType: ReportType;
};

export function ReportMatrixPage({ reportType }: ReportMatrixPageProps) {
  const gateway = useApplicationGateway();
  const [matrix, setMatrix] = useState<ReportMatrixContract | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setMatrix(null);
    setError(null);

    void gateway
      .getReportMatrix({ report_type: reportType })
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
  }, [gateway, reportType]);

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
      key={matrix.report_type}
      gateway={gateway}
      matrix={matrix}
      onChange={setMatrix}
    />
  );
}
