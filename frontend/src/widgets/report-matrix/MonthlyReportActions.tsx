import { ReportSigningDialog } from "./ReportSigningDialog";
import { useEffect, useState } from "react";
import type { ApplicationGateway, ExportRequest, ReportVerification } from "../../shared/api/application-gateway";

const months = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];

export function MonthlyReportActions({ gateway, query, revision, title, blocked, weeks = {} }: {
  weeks?: Record<string, string>; gateway: ApplicationGateway; query: ExportRequest; revision: string; title?: string; blocked: boolean;
}) {
  const [month, setMonth] = useState(new Date().getMonth() + 1);
  const [verification, setVerification] = useState<ReportVerification | null>(null);
  const [busy, setBusy] = useState(false);
  const [opened, setOpened] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const { report_type, organization_id, year } = query;
  const selectedWeek = weeks[`${year ?? new Date().getFullYear()}-${String(month).padStart(2, "0")}`];
  const weekQuery = selectedWeek ? { week_start: selectedWeek } : {};
  useEffect(() => {
    let active = true;
    setVerification(null); setError(""); setMessage(""); setOpened(false);
    if (!blocked && gateway.getReportVerification) {
      void gateway.getReportVerification({ report_type, organization_id, ...(year ? { year } : {}), month, ...weekQuery, expected_revision: revision })
        .then(result => { if (active) setVerification(result); })
        .catch(reason => { if (active) setError(String(reason instanceof Error ? reason.message : reason)); });
    }
    return () => { active = false; };
  }, [gateway, report_type, organization_id, year, month, revision, title, blocked, selectedWeek]);
  if (!gateway.exportPdf || !gateway.getReportVerification || !gateway.verifyReport || !gateway.listReportSigners || !gateway.createReportSigner) return null;

  async function pdf() {
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await gateway.exportPdf!({ ...query, month, ...weekQuery, expected_revision: revision });
      if (!result.cancelled) setMessage(`PDF сохранён: ${result.file_name}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  async function verify(signerId: string, pin: string) {
    if (!verification) return;
    setBusy(true); setError("");
    try {
      const result = await gateway.verifyReport!({ ...query, month, ...weekQuery, expected_revision: revision, signer_id: signerId, pin, confirmed: true, snapshot_sha256: verification.snapshot_sha256 });
      setVerification(result); setOpened(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  return <section className="monthly-report-actions" aria-label="Печать и подтверждение отчёта">
    <label>Месяц печати и проверки<select value={month} disabled={busy} onChange={e => setMonth(Number(e.target.value))}>
      {months.map((name, index) => <option key={name} value={index + 1}>{name} {year ?? new Date().getFullYear()}</option>)}
    </select></label>
    <button className="button secondary" disabled={blocked || busy} onClick={() => void pdf()}>Печать / PDF А4</button>
    <button className="button secondary" disabled={blocked || busy || !verification} onClick={() => { setError(""); setOpened(true); }}>Подтвердить данные</button>
    <p role="status">{blocked ? "Сначала завершите ввод и сохраните изменения." : verification?.status === "VERIFIED" ? `Подтверждено: ${verification.signer_name}, ${verification.signed_at} · ключ ${verification.key_fingerprint}` : verification?.status === "STALE" ? "Данные изменились — требуется повторная проверка." : verification?.status === "INVALID" ? "Подпись недействительна — проверка целостности не пройдена." : verification?.status === "LEGACY" ? "Прежняя отметка без криптографической подписи. Подтвердите данные заново." : "Данные не подтверждены."} {message}</p>
    {error && !opened && <p role="alert" style={{ whiteSpace: "pre-wrap", maxHeight: "35vh", overflowY: "auto" }}>{error}</p>}
    {opened && <ReportSigningDialog gateway={gateway} busy={busy} error={error} month={months[month - 1] ?? String(month)} onClose={() => { setOpened(false); setError(""); }} onSign={verify} />}

  </section>;
}
