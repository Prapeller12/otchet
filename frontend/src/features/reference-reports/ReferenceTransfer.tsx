import { ImportReconciliation } from "./ImportReconciliation";
import { UiIcon } from "../../shared/ui/UiIcon";
import { useEffect, useRef, useState } from "react";
import type { ApplicationGateway, ImportMode, ImportPreview, ReferenceWorkbook, ReportMatrixContract, TransferMapping, TransferSource, TransferPeriodRule, TransferStructureOverride } from "../../shared/api/application-gateway";
import "./reference-report.css";

type Choice = { row: string; period: string; quantity: string; skip: boolean; reason: string };
const MODE_LABELS: Record<ImportMode, string> = { create: "Создать отчёт", append: "Дополнить отчёт", update: "Обновить выбранные значения" };
const METRIC_LABELS: Record<string, string> = { OPENING: "Начальный остаток", RECEIVED: "Поступление", USED: "Расход", SUPPLIED: "Поставка изготовителя", PRODUCED: "Изготовлено", PLAN: "План", FACT: "Факт", COMPLETION: "Выполнение плана", STOCK: "Остаток на складе", VARIANCE: "Отклонение" };
const ROLE_LABELS: Record<string, string> = { REQUISITE: "Реквизит", STRUCTURE: "Структура", INPUT: "Вводимое значение", FORMULA: "Формула", CONTROL: "Контрольный итог", DECORATION: "Оформление" };

function actionSummary(action: Record<string, unknown>): string {
  const labels: Record<string, string> = { CREATE_POSITION: "Создать позицию", UPDATE_POSITION: "Обновить позицию", UPSERT_POSITION: "Сопоставить или создать позицию", UPSERT_PRESENTATION: "Обновить шапку и планы отчёта" };
  const position = typeof action.position === "object" && action.position !== null ? action.position as Record<string, unknown> : {};
  const name = typeof action.position === "string" ? action.position : position.name;
  const suppliers = Array.isArray(action.suppliers) ? action.suppliers.map(item => typeof item === "object" && item !== null ? String(item.name ?? item.manufacturer ?? "") : String(item)).filter(Boolean) : [];
  return [labels[String(action.kind)] ?? "Изменить структуру отчёта", action.designation ?? position.code, name,
    position.norm ? `входимость: ${position.norm}` : "", position.parent_code ? `родительская позиция: ${position.parent_code}` : "",
    suppliers.length ? `изготовители: ${suppliers.join(", ")}` : "", action.suppliers_added ? `новых изготовителей: ${action.suppliers_added}` : ""].filter(Boolean).join(" · ");
}

export function ReferenceTransfer({ book, matrix, gateway, preview, onReady }: {
  book: ReferenceWorkbook; matrix: ReportMatrixContract; gateway: ApplicationGateway;
  preview?: ImportPreview; onReady: (preview: ImportPreview) => void;
}) {
  const [result, setResult] = useState<ImportPreview>(preview ?? { cancelled: false, reference_workbook: book });
  const [mode, setMode] = useState<ImportMode>(preview?.mode ?? "append");
  const [structureOverrides, setStructureOverrides] = useState<Record<string, TransferStructureOverride>>({});
  const [sheetDecisions, setSheetDecisions] = useState<Record<string, { include: boolean; reason: string }>>({});
  const [periodRules, setPeriodRules] = useState<Record<string, TransferPeriodRule>>({});
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [onlyIssues, setOnlyIssues] = useState(false);
  const mounted = useRef(true);
  const sequence = useRef(0);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; sequence.current++; }; }, []);
  const targetMatrix = result.target_matrix ?? matrix;
  const rows = targetMatrix.rows.filter(row => row.cells.some(cell => cell.state.access === "editable"));
  const sources = Object.entries(result.sources ?? result.recognition?.sources ?? book.recognition?.sources ?? {});
  const issueMap = new Map((result.issues ?? []).filter(issue => issue.source_cell).map(issue => [issue.source_cell!, issue.message]));
  function initialChoice(key: string, source: TransferSource): Choice {
    const automatic = result.auto_mappings?.find(mapping => mapping.source === key);
    const coordinate = automatic?.coordinate ?? source.coordinate;
    const coordinateKey = (value: unknown) => JSON.stringify(Object.entries(value as Record<string, unknown> ?? {}).sort(([a], [b]) => a.localeCompare(b)));
    const row = coordinate ? rows.find(row => row.cells.some(cell => coordinateKey(cell.coordinate) === coordinateKey(coordinate))) : undefined;
    const cell = row?.cells.find(cell => coordinateKey(cell.coordinate) === coordinateKey(coordinate));
    return { row: row?.id ?? "", period: cell?.column_id ?? "", quantity: automatic?.quantity ?? source.value, skip: false, reason: "" };
  }
  const choice = (key: string, source: TransferSource) => choices[key] ?? initialChoice(key, source);
  function invalidated(): ImportPreview { const { batch_id, status, error_count, transfer_validated, ...rest } = result; return rest; }
  function publish(next: ImportPreview) { setResult(next); onReady(next); }
  function update(key: string, source: TransferSource, patch: Partial<Choice>) {
    setChoices(old => ({ ...old, [key]: { ...choice(key, source), ...patch } }));
    publish({ ...invalidated(), validation_pending: true,
      issues: (result.issues ?? []).filter(issue => issue.source_cell !== key) });
    setError("");
  }
  async function check(nextMode = mode, overrides = choices) {
    if (!gateway.referenceReport) { setError("Проверка импорта недоступна. Перезапустите полную сборку программы."); return; }
    const requestId = ++sequence.current;
    setBusy(true); setError("");
    publish({ ...invalidated(), validation_pending: true, mode: nextMode });
    try {
      // Server recognition is authoritative. Only deliberate user overrides are sent.
      const mappings: TransferMapping[] = Object.entries(overrides).map(([source, c]) => {
        if (c.skip) return { source, skip_reason: c.reason };
        const cell = rows.find(row => row.id === c.row)?.cells.find(cell => cell.column_id === c.period);
        const original = result.sources?.[source] ?? result.recognition?.sources?.[source] ?? book.recognition?.sources?.[source];
        const planned = !c.row && !c.period ? result.auto_mappings?.find(item => item.source === source)?.coordinate ?? original?.coordinate : undefined;
        const coordinate = cell?.coordinate ?? planned;
        return { source, ...(coordinate ? { coordinate } : {}), quantity: c.quantity, confirmed: Boolean(coordinate) };
      });
      const next = await gateway.referenceReport({ action: "transfer", id: book.id, organization_id: matrix.organization_id,
        report_type: matrix.report_type, ...(matrix.year ? { year: matrix.year } : {}), mode: nextMode, mappings, period_rules: periodRules, sheet_decisions: sheetDecisions, structure_overrides: structureOverrides }) as ImportPreview;
      if (!mounted.current || requestId !== sequence.current) return;
      if (next.applied_period_rules) setPeriodRules(next.applied_period_rules);
      if (next.applied_sheet_decisions) setSheetDecisions(next.applied_sheet_decisions);
      publish({ ...next, cancelled: false, mode: nextMode, reference_workbook: book, file_name: next.file_name ?? book.file_name, validation_pending: false, transfer_validated: true });
    } catch (reason) {
      if (!mounted.current || requestId !== sequence.current) return;
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      publish({ ...invalidated(), status: "INVALID", validation_pending: false, error_count: 1,
        issues: [{ source_cell: null, code: "VALIDATION_FAILED", message }] });
    } finally { if (mounted.current && requestId === sequence.current) setBusy(false); }
  }
  useEffect(() => { void check(); }, [book.id]);
  const visible = sources.filter(([key, source]) => {
    const context = [source.sheet, source.address, source.position?.name, source.position?.code, source.position?.manufacturer, source.metric].filter(Boolean).join(" ");
    return context.toLocaleLowerCase("ru").includes(filter.toLocaleLowerCase("ru")) && (!onlyIssues || issueMap.has(key) || Boolean(source.errors?.length));
  });
  const maxPage = Math.max(0, Math.ceil(visible.length / 30) - 1);
  const currentPage = Math.min(page, maxPage);
  const recognition = result.recognition ?? book.recognition;
  return <section className="transfer-mapping" aria-busy={busy}>
    <h4>Проверка переноса в рабочий отчёт</h4>
    <p>Известные поля сопоставляются автоматически. Проверьте весь пакет и исправьте только отмеченные неоднозначности. Пустые исходные ячейки при обновлении не очищают заполненные значения.</p>
    <label>Режим переноса <select aria-label="Режим переноса" disabled={busy} value={mode} onChange={event => {
      const next = event.target.value as ImportMode; setMode(next); setChoices({}); setPage(0); void check(next, {});
    }}>{Object.entries(MODE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    {recognition?.profile_id && <p>Профиль: {recognition.profile_id} · версия {recognition.profile_version ?? "—"}</p>}
    {result.profile_reused && <p role="status">Использован сохранённый профиль сопоставления. Проверьте значения и подтвердите весь пакет.</p>}
    {error && <p role="alert">{error}</p>}
    {busy && <p role="status">Распознавание и проверка рабочих соответствий…</p>}
    {result.validation_pending && !busy && <p role="status">Соответствия изменены. Выполните повторную проверку перед импортом.</p>}
    {recognition?.sheets?.some(sheet => sheet.decision_required) && <fieldset className="transfer-sheet-decisions"><legend>Неизвестные скрытые листы</legend>
      <p>Для каждого листа выберите, включить его в проверку или исключить с указанной причиной.</p>
      {recognition.sheets.filter(sheet => sheet.decision_required).map(sheet => {
        const decision = sheetDecisions[String(sheet.index)];
        function change(include: boolean, reason: string) { setSheetDecisions(current => ({ ...current, [String(sheet.index)]: { include, reason } })); publish({ ...invalidated(), validation_pending: true }); }
        return <div className="transfer-period-block" key={sheet.index}><strong>{sheet.name}</strong>
          <label>Действие<select aria-label={`Действие для листа ${sheet.name}`} disabled={busy} value={decision ? (decision.include ? "include" : "exclude") : ""} onChange={event => { if (event.target.value) change(event.target.value === "include", decision?.reason ?? ""); }}>
            <option value="">Выберите действие</option><option value="include">Включить в проверку</option><option value="exclude">Исключить с причиной</option></select></label>
          {decision && <label>{decision.include ? "Основание включения" : "Причина исключения"}<input aria-label={`${decision.include ? "Основание включения" : "Причина исключения"} листа ${sheet.name}`} disabled={busy} value={decision.reason} onChange={event => change(decision.include, event.target.value)} /></label>}</div>;
      })}</fieldset>}
    {recognition?.structural_actions?.some(action => Array.isArray(action.errors) && action.errors.length > 0) && <details open><summary>Уточнить структуру позиций</summary>
      <p>Измените только спорные реквизиты и укажите основание. Исправление относится к показанной исходной позиции.</p>
      {recognition.structural_actions.filter(action => Array.isArray(action.errors) && action.errors.length > 0).map(action => {
        const key = String(action.source_key);
        const position = typeof action.position === "object" && action.position !== null ? action.position as Record<string, unknown> : {};
        const current = structureOverrides[key] ?? { code: String(position.code ?? ""), name: String(position.name ?? ""), manufacturer: String(position.manufacturer ?? ""), norm: String(position.norm ?? ""), parent_code: String(position.parent_code ?? ""), reason: "" };
        const fields = { code: "Обозначение", name: "Наименование", manufacturer: "Изготовитель", norm: "Входимость", parent_code: "Родительская позиция", reason: "Основание исправления" } as const;
        const index = Number(action.sheet_index ?? key.split(":")[0]);
        return <fieldset key={key} className="transfer-period-block"><legend>{book.sheets[index]?.name ?? "Лист"} · исходная строка {key.split(":")[1]} · {String(position.code ?? "")}</legend>
          {(action.errors as { code: string; message: string }[]).map(issue => <p key={issue.code} role="status">{issue.message}</p>)}
          {Object.entries(fields).map(([field, label]) => <label key={field}>{label}<input aria-label={`${label} позиции ${key}`} disabled={busy} value={current[field as keyof TransferStructureOverride] ?? ""} onChange={event => {
            setStructureOverrides(previous => ({ ...previous, [key]: { ...current, [field]: event.target.value } })); publish({ ...invalidated(), validation_pending: true });
          }} /></label>)}</fieldset>;
      })}</details>}
    {(recognition?.period_blocks?.length ?? 0) > 0 && <details open className="transfer-period-blocks"><summary>Уточнить границы периодов для целых столбцов ({recognition!.period_blocks!.length})</summary>
      <p>Укажите реальные даты исходной недели. Правило применяется ко всем позициям столбца. Номер недели сам по себе не определяет её границы.</p>
      {recognition!.period_blocks!.map(block => {
        const rule = periodRules[block.key] ?? { start: "", end: "", calendar: "USER_CONFIRMED" as const, reason: "" };
        const change = (patch: Partial<TransferPeriodRule>) => { setPeriodRules(current => ({ ...current, [block.key]: { ...rule, ...patch } })); publish({ ...invalidated(), validation_pending: true }); };
        return <div className="transfer-period-block" key={block.key}><strong>{block.sheet} · {block.address}: {block.label}</strong>
          <label>Начало периода<input aria-label={`Начало периода ${block.key}`} type="date" disabled={busy} value={rule.start} onChange={event => change({ start: event.target.value })} /></label>
          <label>Конец периода<input aria-label={`Конец периода ${block.key}`} type="date" disabled={busy} value={rule.end} onChange={event => change({ end: event.target.value })} /></label>
          <label>Основание выбора дат<input aria-label={`Основание периода ${block.key}`} disabled={busy} value={rule.reason} onChange={event => change({ reason: event.target.value })} /></label>
          {block.errors?.map(issue => <small key={issue.code}>{issue.message}</small>)}</div>;
      })}</details>}
    <div className="reference-toolbar"><label>Найти позицию или поле <input value={filter} onChange={event => { setFilter(event.target.value); setPage(0); }} /></label>
      <label><input type="checkbox" checked={onlyIssues} onChange={event => { setOnlyIssues(event.target.checked); setPage(0); }} />Только ошибки и неоднозначности</label></div>
    <div className="reference-scroll" tabIndex={0} role="region" aria-label="Соответствия Excel и рабочего отчёта"><table className="transfer-table"><thead><tr><th>Источник и назначение</th><th>Исходное → новое</th><th>Рабочая позиция и показатель</th><th>Период</th><th>Результат проверки</th></tr></thead><tbody>
      {visible.slice(currentPage * 30, (currentPage + 1) * 30).map(([key, source]) => {
        const c = choice(key, source); const issue = result.validation_pending && choices[key] ? undefined : issueMap.get(key) ?? source.errors?.map(error => error.message).join("; ");
        const automatic = Boolean(source.coordinate || result.auto_mappings?.some(mapping => mapping.source === key));
        const previousCell = targetMatrix.rows.find(row => row.id === c.row)?.cells.find(cell => cell.column_id === c.period);
        const previous = previousCell ? (previousCell.value.kind === "QUANTITY" ? previousCell.value.quantity : "данные не представлены") : "соответствие ещё не определено";
        const label = source.position ? [source.position.code, source.position.name, source.position.manufacturer].filter(Boolean).join(" · ") : "";
        return <tr key={key} className={issue ? "transfer-invalid" : ""}>
          <td><strong>{label}</strong><br />{source.sheet} · {source.address}<br />{ROLE_LABELS[source.role] ?? source.role}{source.unit ? ` · ${source.unit}` : ""}{source.formula && <small>Формула: {source.formula}</small>}</td>
          <td><span>Исходное: {source.value || "пусто"}</span><br /><span>В рабочем отчёте: {previous}</span><input aria-label={`Значение ${key}`} disabled={busy || c.skip} value={c.quantity} onChange={event => update(key, source, { quantity: event.target.value })} /></td>
          <td><select aria-label={`Показатель ${key}`} aria-invalid={Boolean(issue)} disabled={busy || c.skip} value={c.row} onChange={event => update(key, source, { row: event.target.value, period: "" })}>
            <option value="">{automatic ? "Новая позиция по профилю" : "Выберите позицию и показатель"}</option>{rows.map(row => <option key={row.id} value={row.id}>{Object.values(row.left_values).join(" · ")}</option>)}</select>{source.metric && <small>{METRIC_LABELS[source.metric] ?? "Показатель исходного отчёта"}</small>}</td>
          <td>{source.period && <p>{source.period.start} — {source.period.end}</p>}<select aria-label={`Период ${key}`} disabled={busy || c.skip} value={c.period} onChange={event => update(key, source, { period: event.target.value })}>
            <option value="">{automatic ? "Период определён по профилю" : "Выберите период"}</option>{targetMatrix.time_columns.map(period => <option key={period.id} value={period.id}>{period.group_label} · {period.label}</option>)}</select></td>
          <td>{issue ? <small role="status">{issue}</small> : <span>{choices[key] ? "Изменено — проверьте пакет" : automatic ? "Автоматическое сопоставление" : "Требуется сопоставление"}</span>}<label><input type="checkbox" disabled={busy} checked={c.skip} onChange={event => update(key, source, { skip: event.target.checked })} />Не переносить</label>
            {c.skip && <input aria-label={`Причина исключения ${key}`} placeholder="Обязательная причина исключения" disabled={busy} value={c.reason} onChange={event => update(key, source, { reason: event.target.value })} />}</td>
        </tr>;
      })}
    </tbody></table></div>
    {!busy && !sources.length && <p>Сервер не вернул вводимых значений. Проверьте сообщения и перечень структурных изменений.</p>}
    <div className="reference-toolbar"><button disabled={busy || currentPage === 0} onClick={() => setPage(currentPage - 1)}><UiIcon name="arrow-left" />Предыдущие поля</button>
      <span>Поля {visible.length ? currentPage * 30 + 1 : 0}–{Math.min((currentPage + 1) * 30, visible.length)} из {visible.length}</span>
      <button disabled={busy || currentPage >= maxPage} onClick={() => setPage(currentPage + 1)}><UiIcon name="arrow-right" />Следующие поля</button>
      <button disabled={busy} onClick={() => void check()}>{busy ? "Проверка…" : "Проверить сопоставление"}</button></div>
    {(result.structural_actions ?? recognition?.structural_actions)?.length ? <details><summary>Создаваемые и изменяемые объекты ({(result.structural_actions ?? recognition?.structural_actions)!.length})</summary><ul>{(result.structural_actions ?? recognition?.structural_actions)!.map((action, index) => <li key={index}>{actionSummary(action)}</li>)}</ul></details> : null}
    {result.reconciliation && <ImportReconciliation rows={result.reconciliation} />}
    {recognition?.fields && <details><summary>Классификация исходных полей</summary><ul>{Object.entries(recognition.fields).map(([key, field]) => <li key={key}>{field.sheet} · {field.address}: {ROLE_LABELS[field.role] ?? field.role} · {field.value || "пусто"}{field.unit ? ` · ${field.unit}` : ""}</li>)}</ul></details>}
  </section>;
}
