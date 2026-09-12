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
  const [signer, setSigner] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const { report_type, organization_id, year } = query;
  const selectedWeek = weeks[`${year ?? new Date().getFullYear()}-${String(month).padStart(2, "0")}`];
  const weekQuery = selectedWeek ? { week_start: selectedWeek } : {};
  useEffect(() => {
    let active = true;
    setVerification(null); setError(""); setMessage(""); setOpened(false); setConfirmed(false);
    if (!blocked && gateway.getReportVerification) {
      void gateway.getReportVerification({ report_type, organization_id, ...(year ? { year } : {}), month, ...weekQuery, expected_revision: revision })
        .then(result => { if (active) setVerification(result); })
        .catch(reason => { if (active) setError(String(reason instanceof Error ? reason.message : reason)); });
    }
    return () => { active = false; };
  }, [gateway, report_type, organization_id, year, month, revision, title, blocked, selectedWeek]);
  if (!gateway.exportPdf || !gateway.getReportVerification || !gateway.verifyReport) return null;

  async function pdf() {
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await gateway.exportPdf!({ ...query, month, ...weekQuery, expected_revision: revision });
      if (!result.cancelled) setMessage(`PDF сохранён: ${result.file_name}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  async function verify() {
    if (!verification) return;
    setBusy(true); setError("");
    try {
      const result = await gateway.verifyReport!({ ...query, month, ...weekQuery, expected_revision: revision, signer_name: signer.trim(), confirmed, snapshot_sha256: verification.snapshot_sha256 });
      setVerification(result); setOpened(false); setConfirmed(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  return <section className="monthly-report-actions" aria-label="Печать и подтверждение отчёта">
    <label>Месяц печати и проверки<select value={month} disabled={busy} onChange={e => setMonth(Number(e.target.value))}>
      {months.map((name, index) => <option key={name} value={index + 1}>{name} {year ?? new Date().getFullYear()}</option>)}
    </select></label>
    <button className="button secondary" disabled={blocked || busy} onClick={() => void pdf()}>Печать / PDF А4</button>
    <button className="button secondary" disabled={blocked || busy || !verification} onClick={() => { setOpened(true); setConfirmed(false); }}>Подтвердить данные</button>
    <p role="status">{blocked ? "Сначала завершите ввод и сохраните изменения." : verification?.status === "VERIFIED" ? `Подтверждено: ${verification.signer_name}, ${verification.signed_at}` : verification?.status === "STALE" ? "Данные изменились — требуется повторная проверка." : "Данные не подтверждены."} {message}</p>
    {error && !opened && <p role="alert" style={{ whiteSpace: "pre-wrap", maxHeight: "35vh", overflowY: "auto" }}>{error}</p>}
    {opened && <div className="excel-dialog-backdrop"><section className="excel-dialog verification-form" role="dialog" aria-modal="true" aria-labelledby="verification-title">
      <h3 id="verification-title">Подтверждение данных — {months[month - 1]}</h3>
      <p>Подтверждается сохранённый отчёт за выбранный месяц вместе со сводными данными с начала года.</p>
      <label>ФИО руководителя<input type="text" autoFocus maxLength={120} value={signer} disabled={busy} onChange={e => setSigner(e.target.value)} /></label>
      <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} /> Я проверил данные и подтверждаю их верность.</label>
      <p>Локальная отметка с ФИО, введёнными вручную. Личность не проверяется; электронная подпись не создаётся.</p>
      {error && <p role="alert" style={{ whiteSpace: "pre-wrap", maxHeight: "35vh", overflowY: "auto" }}>{error}</p>}
      <div className="excel-dialog-actions">
        <button className="button secondary" disabled={busy} onClick={() => setOpened(false)}>Отмена</button>
        <button className="button primary" disabled={busy || !confirmed || !signer.trim() || blocked} onClick={() => void verify()}>{busy ? "Подтверждение…" : "Подтверждаю верность данных"}</button>
      </div>
    </section></div>}
  </section>;
}
