import { useEffect, useState } from "react";
import type { ApplicationGateway, ExportRequest, ReportVerification } from "../../shared/api/application-gateway";

/** Status only: Save and Print own their automatic confirmation workflow. */
export function MonthlyReportActions({ gateway, query, revision, title, blocked, weeks = {}, controlledMonth, refresh = 0 }: {
  weeks?: Record<string, string>; gateway: ApplicationGateway; query: ExportRequest; revision: string; title?: string; blocked: boolean;
  controlledMonth: string; refresh?: number;
}) {
  const [verification, setVerification] = useState<ReportVerification | null>(null);
  const [error, setError] = useState("");
  const { report_type, organization_id, year } = query;
  const month = Number(controlledMonth.slice(5, 7)) || 1;
  const selectedWeek = weeks[controlledMonth];
  useEffect(() => {
    let active = true;
    setVerification(null); setError("");
    if (!blocked && gateway.getReportVerification) {
      void gateway.getReportVerification({ report_type, organization_id, ...(year ? { year } : {}), month,
        ...(selectedWeek ? { week_start: selectedWeek } : {}), expected_revision: revision })
        .then(result => { if (active) setVerification(result); })
        .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); });
    }
    return () => { active = false; };
  }, [gateway, report_type, organization_id, year, month, revision, title, blocked, selectedWeek, refresh]);
  if (!gateway.getReportVerification) return null;
  return <section className="monthly-report-actions" aria-label="Подтверждение отчёта">
    <p role="status">{blocked ? "Есть изменения. Сохраните их перед печатью." : verification?.status === "VERIFIED"
      ? `Подтверждено: ${verification.signer_name}, ${verification.signed_at} · ключ ${verification.key_fingerprint}`
      : verification?.status === "STALE" ? "Данные изменились. Подтверждение будет запрошено при сохранении или печати."
      : verification?.status === "INVALID" ? "Подпись недействительна — проверка целостности не пройдена."
      : "При сохранении или печати потребуется код ответственного лица."}</p>
    {error && <p role="alert">{error}</p>}
  </section>;
}
