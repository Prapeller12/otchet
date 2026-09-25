import { UiIcon } from "../../shared/ui/UiIcon";
import { CompactProductionHeader } from "./CompactProductionHeader";
import { useEffect, useRef, useState } from "react";
import { HintValue } from "../../shared/ui/FieldHint";
import { REPORT_FIELD_HINTS } from "../../shared/config/report-field-hints";

export type ReportHeaderFields = {
  product_designation: string; product_name: string; factory_name: string; product_image: string;
};
export type ProductionCode = {
  id: string; label: string; plans: Record<string, string>; actuals: Record<string, string>;
};
export type ProductionHeaderPresentation = {
  header?: ReportHeaderFields;
  production_codes?: ProductionCode[];
  production_code_annual?: Record<string, string>;
  annual?: { plan: string; actual: string; completion: string; plan_months: number; actual_months: number };
};
export type ProductionHeaderPatch = {
  header: ReportHeaderFields; production_codes?: ProductionCode[]; confirm_production_totals?: boolean;
};
const EMPTY_HEADER: ReportHeaderFields = { product_designation: "", product_name: "", factory_name: "", product_image: "" };

export type ProductionHeaderProps = {
  workspaceMode?: "entry" | "report-settings" | "admin";
  onSaveReady?(save: (() => Promise<void>) | null): void;
  presentation: ProductionHeaderPresentation; year: number; headSite: boolean; blocked: boolean;
  onSave(patch: ProductionHeaderPatch): Promise<void>;
  onDirtyChange(dirty: boolean): void; onBusyChange?(busy: boolean): void;
};

export function ProductionHeader(props: ProductionHeaderProps) {
  if (props.workspaceMode === "entry") return <section className="readonly-production-header" aria-label="Шапка изделия">
    <span>Годовой план: <HintValue hint={REPORT_FIELD_HINTS.annualPlan}><output aria-label="Годовой план">{props.presentation.annual?.plan || "—"}</output></HintValue></span>
    <span>Название изделия: <HintValue hint={REPORT_FIELD_HINTS.productName}><strong>{props.presentation.header?.product_name || "Не задано"}</strong></HintValue></span>
    <span>Шифр: <HintValue hint={REPORT_FIELD_HINTS.productCode}><strong>{props.presentation.header?.product_designation || "Не задан"}</strong></HintValue></span>
  </section>;
  if (props.workspaceMode === "admin" || props.workspaceMode === "report-settings") return <DetailedProductionHeader {...props} />;
  return props.headSite ? <CompactProductionHeader {...props} /> : <DetailedProductionHeader {...props} />;
}

function DetailedProductionHeader({ presentation, year, headSite, blocked, onSave, onDirtyChange, onBusyChange, onSaveReady }: ProductionHeaderProps) {
  const baseline = JSON.stringify({ header: { ...EMPTY_HEADER, ...presentation.header }, codes: presentation.production_codes ?? [] });
  const [draft, setDraft] = useState<{header: ReportHeaderFields; codes: ProductionCode[]}>(() => JSON.parse(baseline));
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const fileReader = useRef<FileReader | null>(null);
  const dirty = JSON.stringify(draft) !== baseline;
  useEffect(() => { setDraft(JSON.parse(baseline)); setConfirmed(false); }, [baseline]);
  useEffect(() => { onDirtyChange(dirty || reading); }, [dirty, reading, onDirtyChange]);
  useEffect(() => () => { fileReader.current?.abort(); }, []);
  const months = Array.from({ length: 12 }, (_, i) => `${year}-${String(i + 1).padStart(2, "0")}`);
  const disabled = blocked || busy || reading;
  function editHeader(patch: Partial<ReportHeaderFields>) {
    setDraft(previous => ({ ...previous, header: { ...previous.header, ...patch } }));
  }
  function editCode(id: string, field: "plans" | "actuals", month: string, value: string) {
    setDraft(previous => ({ ...previous, codes: previous.codes.map(code => code.id === id ? {
      ...code, [field]: { ...code[field], [month]: value.replace(",", ".") },
    } : code) }));
    setConfirmed(false);
  }
  function upload(file?: File) {
    if (!file) return;
    if (!["image/png", "image/jpeg"].includes(file.type) || file.size > 2 * 1024 * 1024) {
      setError("Выберите PNG или JPEG размером до 2 МБ."); return;
    }
    const reader = new FileReader(); fileReader.current = reader;
    setReading(true); onBusyChange?.(true); setError("");
    reader.onload = () => { editHeader({ product_image: String(reader.result) }); setReading(false); onBusyChange?.(false); };
    reader.onerror = () => { setError("Не удалось прочитать изображение."); setReading(false); onBusyChange?.(false); };
    reader.onabort = () => { setReading(false); onBusyChange?.(false); };
    reader.readAsDataURL(file);
  }
  async function save() {
    if (!dirty || disabled) return;
    if (draft.codes.some(code => !code.label.trim())) { setError("Введите название каждого кода выпуска или уберите пустой код."); return; }
    setBusy(true); onBusyChange?.(true); setError("");
    try {
      await onSave({ header: draft.header, ...(headSite ? { production_codes: draft.codes, confirm_production_totals: confirmed } : {}) });
      setConfirmed(false);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); onBusyChange?.(false); }
  }
  useEffect(() => { onSaveReady?.(save); return () => onSaveReady?.(null); });
  return <section className="production-header" aria-label="Шапка отчёта и выпуск изделий">
    <fieldset disabled={disabled}>
      <legend>Изделие и изготовитель</legend>
      <div className="production-header-fields">
        <label>Шифр изделия<input maxLength={200} value={draft.header.product_designation} onChange={e => editHeader({ product_designation: e.target.value })} /></label>
        <label>Наименование изделия<input maxLength={200} value={draft.header.product_name} onChange={e => editHeader({ product_name: e.target.value })} /></label>
        <label>Завод / изготовитель<input maxLength={200} value={draft.header.factory_name} onChange={e => editHeader({ factory_name: e.target.value })} /></label>
        <label>Изображение изделия<input type="file" accept="image/png,image/jpeg" onChange={e => upload(e.target.files?.[0])} /></label>
        {draft.header.product_image && <div className="position-picture"><img src={draft.header.product_image} alt="Изображение изделия" />
          <button type="button" className="mini-button" onClick={() => editHeader({ product_image: "" })}><UiIcon name="trash" />Убрать изображение изделия</button></div>}
      </div>
      {presentation.annual && <div className="production-annual" aria-label="Годовые итоги">
        <span>Годовой план: <HintValue hint={REPORT_FIELD_HINTS.annualPlan}><strong>{presentation.annual.plan || "—"}</strong> шт.</HintValue></span>
        <span>Выпущено за год: <HintValue hint={REPORT_FIELD_HINTS.annualActual}><strong>{presentation.annual.actual || "—"}</strong> шт.</HintValue></span>
        <span>Выполнение годового плана: <HintValue hint={REPORT_FIELD_HINTS.annualCompletion}><strong>{presentation.annual.completion ? presentation.annual.completion + " %" : "—"}</strong></HintValue></span>
        <small>По сохранённым данным: план за {presentation.annual.plan_months} мес., факт за {presentation.annual.actual_months} мес. Пустые месяцы не считаются нулями.</small>
      </div>}
      {headSite && <>
        <h3>План и факт выпуска готовых изделий по кодам</h3>
        <p className="field-help">Названия кодов задаются пользователем. В каждом месяце сумма по кодам используется для расчёта общего выпуска и расхода составных частей. Заполните все коды месяца; подтверждённое отсутствие выпуска обозначьте нулём.</p>
        {draft.codes.length > 0 && <div className="production-code-scroll" tabIndex={0} role="region" aria-label="Месячный выпуск по кодам">
          <table className="production-code-table"><thead><tr><th scope="col" rowSpan={2}>Код / модификация</th><th scope="col" rowSpan={2}>Выпущено за год</th>
            {months.map(month => <th scope="colgroup" colSpan={2} key={month}>{new Intl.DateTimeFormat("ru", { month: "long" }).format(new Date(month + "-01T12:00:00"))}</th>)}<th rowSpan={2}>Действия</th></tr>
            <tr>{months.flatMap(month => [<th scope="col" key={month + "p"}>План</th>, <th scope="col" key={month + "f"}>Факт</th>])}</tr></thead>
            <tbody>{draft.codes.map(code => <tr key={code.id}><th scope="row"><input aria-label="Код / модификация" maxLength={200} placeholder="Введите код изделия" value={code.label} onChange={e => setDraft(previous => ({ ...previous, codes: previous.codes.map(row => row.id === code.id ? { ...row, label: e.target.value } : row) }))} /></th>
              <td><HintValue hint={REPORT_FIELD_HINTS.codeAnnual}><output aria-label={`${code.label || "Новый код"}: выпущено за год`}>{presentation.production_code_annual?.[code.id] || "—"}</output></HintValue></td>
              {months.flatMap(month => ( ["plans", "actuals"] as const).map(field => <td key={month + field}><input inputMode="decimal" aria-label={`${code.label || "Новый код"}: ${field === "plans" ? "план" : "факт"} ${month}`} value={code[field][month] ?? ""} onChange={e => editCode(code.id, field, month, e.target.value)} /></td>))}
              <td><button type="button" className="mini-button" disabled={Object.values(code.plans).some(Boolean) || Object.values(code.actuals).some(Boolean)} title="Код с данными нельзя удалить; сначала явно очистите его значения" onClick={() => setDraft(previous => ({ ...previous, codes: previous.codes.filter(row => row.id !== code.id) }))}><UiIcon name="trash" />Убрать код</button></td>
            </tr>)}</tbody></table>
        </div>}
        <button type="button" className="button secondary" disabled={draft.codes.length >= 50} onClick={() => setDraft(previous => ({ ...previous, codes: [...previous.codes, { id: crypto.randomUUID().replaceAll("-", "").toUpperCase(), label: "", plans: {}, actuals: {} }] }))}><UiIcon name="add" />Код выпуска</button>
        {dirty && draft.codes.length > 0 && <label className="production-confirmation"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />При отличии от прежнего общего выпуска использовать суммы по кодам. Прежние общие значения сохранятся в базе.</label>}
      </>}
      <div className="compact-actions">
        {!onSaveReady && <button type="button" className="button primary" disabled={!dirty || draft.codes.some(code => !code.label.trim())} onClick={() => void save()}><UiIcon name="save" />Сохранить шапку и выпуск</button>}
        <button type="button" className="button secondary" disabled={!dirty} onClick={() => { setDraft(JSON.parse(baseline)); setConfirmed(false); setError(""); }}><UiIcon name="undo" />Отменить изменения шапки</button>
        <span role="status">{busy ? "Сохранение…" : dirty ? "Есть изменения. Нажмите «Сохранить» вверху." : "Шапка сохранена"}</span>
      </div>
    </fieldset>
    {error && <p role="alert">{error}</p>}
  </section>;
}
