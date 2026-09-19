import { useEffect, useState } from "react";
import type { ApplicationGateway, ReportMatrixContract } from "../../shared/api/application-gateway";
import type { ProductionHeaderPresentation } from "./ProductionHeader";

export function SubsidiaryControls({ matrix, gateway, blocked, onChange, month, onMonth, week, onWeek, onBusy, onDirtyChange }: {
  matrix: ReportMatrixContract; gateway: ApplicationGateway; blocked: boolean;
  onBusy(busy: boolean): void; onChange(matrix: ReportMatrixContract): void; month: string; onMonth(month: string): void;
  week: string; onWeek(week: string): void;
  onDirtyChange?(dirty: boolean): void;
}) {
  const [actual, setActual] = useState(matrix.presentation?.actuals?.[month] ?? "");
  const [plan, setPlan] = useState(matrix.presentation?.plans?.[month] ?? ""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => setPlan(matrix.presentation?.plans?.[month] ?? ""), [matrix.presentation?.plans, month]);
  useEffect(() => setActual(matrix.presentation?.actuals?.[month] ?? ""), [matrix.presentation?.actuals, month]);
  const months = [...new Set(matrix.time_columns.map(c => c.group_label))];
  const weeks = matrix.time_columns.filter(c => c.group_label === month && c.kind === "USED");
  const codes = (matrix.presentation as ProductionHeaderPresentation | undefined)?.production_codes ?? [];
  const planFromCodes = !!matrix.head_site && codes.some(code => Object.hasOwn(code.plans, month));
  const actualFromCodes = !!matrix.head_site && codes.some(code => Object.hasOwn(code.actuals, month));
  const savedPlan = matrix.presentation?.plans?.[month] ?? "";
  const savedActual = matrix.presentation?.actuals?.[month] ?? "";
  const dirtyPlan = !planFromCodes && plan !== savedPlan;
  const dirtyActual = !actualFromCodes && actual !== savedActual;
  const dirty = dirtyPlan || dirtyActual;
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => { onDirtyChange?.(false); }, [onDirtyChange]);
  async function save() {
    if (busy || blocked || !dirty) return;
    setBusy(true); onBusy(true); setError("");
    try {
      if (!gateway.saveReportPresentation) throw new Error("Сохранение плана недоступно");
      await gateway.saveReportPresentation({ report_type: matrix.report_type, organization_id: matrix.organization_id,
        expected_revision: matrix.matrix_revision,
        ...(dirtyPlan ? { plans: { [month]: plan.replace(",", ".") } } : {}),
        ...(dirtyActual ? { actuals: { [month]: actual.replace(",", ".") } } : {}) });
      onChange(await gateway.getReportMatrix({ report_type: matrix.report_type, organization_id: matrix.organization_id, year: matrix.year! }));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); onBusy(false); }
  }
  return <section className="subsidiary-controls" aria-label="План составной части и неделя остатка">
    <label>Месяц<select value={month} disabled={blocked || busy || dirty} onChange={e => onMonth(e.target.value)}>{months.map(m => <option key={m} value={m}>{new Intl.DateTimeFormat("ru", { month: "long", year: "numeric" }).format(new Date(m + "-01T12:00:00"))}</option>)}</select></label>
    <label>{matrix.head_site ? "План готовых изделий, шт." : "План выпуска, шт."}<input inputMode="decimal" value={plan} disabled={blocked || busy} readOnly={planFromCodes} onChange={e => setPlan(e.target.value)} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); void save(); } }} />{planFromCodes && <small>Из кодов выпуска — измените план в таблице кодов выше.</small>}</label>
    <label>{matrix.head_site ? "Выпущено готовых изделий, шт." : "Выпущено, шт."}<input inputMode="decimal" value={actual} disabled={blocked || busy} readOnly={actualFromCodes} onChange={e => setActual(e.target.value)} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); void save(); } }} />{actualFromCodes && <small>Из кодов выпуска — измените факт в таблице кодов выше.</small>}</label>
    <label>Выполнение<output>{matrix.presentation?.completion?.[month] ? matrix.presentation.completion[month] + " %" : "—"}</output></label>
    <button type="button" className="button primary" disabled={!dirty || blocked || busy} onClick={() => void save()}>Сохранить план и выпуск</button>
    <button type="button" className="button secondary" disabled={!dirty || busy} onClick={() => { setPlan(savedPlan); setActual(savedActual); setError(""); }}>Отменить изменения плана</button>
    <span className="plan-save-status" role="status">{busy ? "Сохранение…" : dirty ? "План и выпуск изменены — сохраните или отмените изменения." : "План и выпуск сохранены"}</span>
    {!matrix.head_site && <label>Остаток на конец недели<select value={week || weeks.at(-1)?.id || ""} disabled={blocked || busy || dirty} onChange={e => onWeek(e.target.value)}>{weeks.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}
    {matrix.head_site ? <p>В таблице укажите месячный план и факт выпуска составных частей. Расход рассчитывается по выпуску готовых изделий и входимости.</p> : <p>Расход вводите по неделям. Остаток показан на {weeks.find(w => w.id === week)?.label.split("–").at(-1) ?? weeks.at(-1)?.label.split("–").at(-1)} число. Нажмите заголовок недели, чтобы посмотреть остаток на её конец.</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
