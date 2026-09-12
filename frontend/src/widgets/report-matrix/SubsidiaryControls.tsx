import { useEffect, useState } from "react";
import type { ApplicationGateway, ReportMatrixContract } from "../../shared/api/application-gateway";

export function SubsidiaryControls({ matrix, gateway, blocked, onChange, month, onMonth, week, onBusy }: {
  matrix: ReportMatrixContract; gateway: ApplicationGateway; blocked: boolean;
  onBusy(busy: boolean): void; onChange(matrix: ReportMatrixContract): void; month: string; onMonth(month: string): void;
  week: string; onWeek(week: string): void;
}) {
  const [actual, setActual] = useState("");
  const [plan, setPlan] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => setPlan(matrix.presentation?.plans?.[month] ?? ""), [matrix.presentation?.plans, month]);
  useEffect(() => setActual(matrix.presentation?.actuals?.[month] ?? ""), [matrix.presentation?.actuals, month]);
  const months = [...new Set(matrix.time_columns.map(c => c.group_label))];
  const weeks = matrix.time_columns.filter(c => c.group_label === month && c.kind === "USED");
  async function save() {
    if (busy || (plan === (matrix.presentation?.plans?.[month] ?? "") && actual === (matrix.presentation?.actuals?.[month] ?? ""))) return;
    setBusy(true); onBusy(true); setError("");
    try {
      if (!gateway.saveReportPresentation) throw new Error("Сохранение плана недоступно");
      await gateway.saveReportPresentation({ report_type: matrix.report_type, organization_id: matrix.organization_id,
        expected_revision: matrix.matrix_revision, plans: { ...matrix.presentation?.plans, [month]: plan.replace(",", ".") }, actuals: { ...matrix.presentation?.actuals, [month]: actual.replace(",", ".") } });
      onChange(await gateway.getReportMatrix({ report_type: matrix.report_type, organization_id: matrix.organization_id, year: matrix.year! }));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); onBusy(false); }
  }
  return <section className="subsidiary-controls" aria-label="План составной части и неделя остатка">
    <label>Месяц<select value={month} disabled={blocked || busy} onChange={e => onMonth(e.target.value)}>{months.map(m => <option key={m} value={m}>{new Intl.DateTimeFormat("ru", { month: "long", year: "numeric" }).format(new Date(m + "-01T12:00:00"))}</option>)}</select></label>
    <label>{matrix.head_site ? "План готовых изделий, шт." : "План выпуска, шт."}<input inputMode="decimal" value={plan} disabled={blocked || busy} onChange={e => setPlan(e.target.value)} onBlur={() => void save()} onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} /></label>
    <label>{matrix.head_site ? "Выпущено готовых изделий, шт." : "Выпущено, шт."}<input inputMode="decimal" value={actual} disabled={blocked || busy} onChange={e => setActual(e.target.value)} onBlur={() => void save()} onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} /></label>
    <label>Выполнение<output>{matrix.presentation?.completion?.[month] ? matrix.presentation.completion[month] + " %" : "—"}</output></label>
    <span className="plan-save-status" role="status">{busy ? "Сохранение…" : "План и выпуск сохраняются автоматически"}</span>
    {matrix.head_site ? <p>В таблице укажите месячный план и факт выпуска составных частей. Расход рассчитывается по выпуску готовых изделий и входимости.</p> : <p>Расход вводите по неделям. Остаток показан на {weeks.find(w => w.id === week)?.label.split("–").at(-1) ?? weeks.at(-1)?.label.split("–").at(-1)} число. Нажмите заголовок недели, чтобы посмотреть остаток на её конец.</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
