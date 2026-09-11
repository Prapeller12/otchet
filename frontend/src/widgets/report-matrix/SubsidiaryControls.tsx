import { useEffect, useState } from "react";
import type { ApplicationGateway, ReportMatrixContract } from "../../shared/api/application-gateway";

export function SubsidiaryControls({ matrix, gateway, blocked, onChange, month, onMonth, week, onWeek, onBusy }: {
  matrix: ReportMatrixContract; gateway: ApplicationGateway; blocked: boolean;
  onBusy(busy: boolean): void; onChange(matrix: ReportMatrixContract): void; month: string; onMonth(month: string): void;
  week: string; onWeek(week: string): void;
}) {
  const [plan, setPlan] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => setPlan(matrix.presentation?.plans?.[month] ?? ""), [matrix.presentation?.plans, month]);
  const months = [...new Set(matrix.time_columns.map(c => c.group_label))];
  const weeks = matrix.time_columns.filter(c => c.group_label === month && c.kind === "USED");
  async function save() {
    setBusy(true); onBusy(true); setError("");
    try {
      if (!gateway.saveReportPresentation) throw new Error("Сохранение плана недоступно");
      await gateway.saveReportPresentation({ report_type: matrix.report_type, organization_id: matrix.organization_id,
        expected_revision: matrix.matrix_revision, plans: { ...matrix.presentation?.plans, [month]: plan.replace(",", ".") } });
      onChange(await gateway.getReportMatrix({ report_type: matrix.report_type, organization_id: matrix.organization_id, year: matrix.year! }));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); onBusy(false); }
  }
  return <section className="subsidiary-controls" aria-label="План составной части и неделя остатка">
    <label>Месяц<select value={month} disabled={blocked || busy} onChange={e => onMonth(e.target.value)}>{months.map(m => <option key={m} value={m}>{new Intl.DateTimeFormat("ru", { month: "long", year: "numeric" }).format(new Date(m + "-01T12:00:00"))}</option>)}</select></label>
    <label>План составной части, шт. (C6)<input inputMode="decimal" value={plan} disabled={blocked || busy} onChange={e => setPlan(e.target.value)} /></label>
    <button type="button" className="button primary" disabled={blocked || busy} onClick={() => void save()}>Сохранить план</button>
    <label>Остаток на конец недели<select value={week || weeks.at(-1)?.id || ""} disabled={busy} onChange={e => onWeek(e.target.value)}>{weeks.map(w => <option key={w.id} value={w.id}>{w.label}</option>)}</select></label>
    <p>Потребность = C6 × входимость. Недельные значения — расход. Дефицит рассчитан по детали за месяц. Если поступлений не было, укажите 0; пустое поле означает, что данные ещё не представлены.</p>
    {error && <p role="alert">{error}</p>}
  </section>;
}
