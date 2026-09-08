import { useMemo, useState } from "react";
import type { ApplicationGateway, ImportPreview, ReferenceWorkbook, ReferenceSheet, ReportMatrixContract } from "../../shared/api/application-gateway";
import "./reference-report.css";

type Choice = { row: string; period: string; quantity: string; confirmed: boolean; skip: boolean; reason: string };
type Source = { key: string; label: string; value: string; context: string };
function sourceText(sheet: ReferenceSheet, column: string, row: number): string {
  const point = (address: string): [number, number] => {
    const letters = address.replace(/[0-9]/g, "");
    return [Array.from(letters).reduce((n, c) => n * 26 + c.charCodeAt(0) - 64, 0), Number(address.slice(letters.length))];
  };
  let address = `${column}${row}`;
  const [x, y] = point(address);
  for (const merge of sheet.merges) {
    const [start, end] = merge.split(":");
    const [x1, y1] = point(start!); const [x2, y2] = point(end ?? start!);
    if (x >= x1 && x <= x2 && y >= y1 && y <= y2) { address = start!; break; }
  }
  return sheet.cells[address]?.display ?? "";
}

export function ReferenceTransfer({ book, matrix, gateway, onReady }: {
  book: ReferenceWorkbook; matrix: ReportMatrixContract; gateway: ApplicationGateway;
  onReady: (preview: ImportPreview) => void;
}) {
  const sources = useMemo(() => book.sheets.flatMap((sheet, index) => Object.entries(sheet.cells).flatMap(([address, cell]): Source[] => {
    const letters = address.replace(/[0-9]/g, "");
    const row = Number(address.slice(letters.length));
    const col = Array.from(letters).reduce((n, l) => n * 26 + l.charCodeAt(0) - 64, 0);
    const quantityRegion = row >= 9 && (col === 5 || col >= 7);
    if ((!Number.isFinite(Number(cell.display)) && !quantityRegion) || cell.display.trim() === "" || [1,7,8].includes(row) || letters === "B" || (row >= 9 && col < 5)) return [];
    return [{ key: `${index}:${address}`, label: `${sheet.name} · ${address}`, value: cell.display,
      context: [sourceText(sheet, "C", row), sourceText(sheet, "D", row), sourceText(sheet, "F", row),
        sourceText(sheet, letters, 7), sourceText(sheet, letters, book.report_type === "HEAD_SITE" ? 1 : 3),
        book.report_type === "SUBSIDIARY" ? sourceText(sheet, letters, 4) : ""].filter(Boolean).join(" · ") }];
  })), [book]);
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [issues, setIssues] = useState<Record<string, string>>({});
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [excludeReason, setExcludeReason] = useState("");
  const visible = sources.filter(s => `${s.context} ${s.label}`.toLocaleLowerCase("ru").includes(filter.toLocaleLowerCase("ru")));

  const rows = matrix.rows.filter(row => row.cells.some(cell => cell.state.access === "editable"));
  const choice = (s: Source): Choice => choices[s.key] ?? { row: "", period: "", quantity: s.value, confirmed: false, skip: false, reason: "" };
  const unassigned = visible.filter(s => !choice(s).row && !choice(s).skip);
  function excludeUnassigned() {
    if (excludeReason.trim().length < 3) return;
    setChoices(old => ({ ...old, ...Object.fromEntries(unassigned.map(s => [s.key,
      { ...choice(s), skip: true, reason: excludeReason.trim() }])) }));
    setIssues({});
  }
  function update(source: Source, patch: Partial<Choice>) {
    setChoices(old => ({ ...old, [source.key]: { ...choice(source), ...patch } }));
    setIssues({});
  }
  async function check() {
    if (!gateway.referenceReport) return;
    setBusy(true); setError("");
    try {
      const result = await gateway.referenceReport({ action: "transfer", id: book.id,
        organization_id: matrix.organization_id, report_type: matrix.report_type,
        ...(matrix.year ? { year: matrix.year } : {}),
        mappings: sources.map(s => {
          const c = choice(s);
          if (c.skip) return { source: s.key, skip_reason: c.reason };
          const cell = rows.find(r => r.id === c.row)?.cells.find(v => v.column_id === c.period);
          return { source: s.key, coordinate: cell?.coordinate ?? {}, quantity: c.quantity, confirmed: c.confirmed };
        }),
      }) as ImportPreview;
      if ((result.error_count ?? 0) > 0) {
        setIssues(Object.fromEntries((result.issues ?? []).map(i => [i.source_cell ?? "", i.message])));
        setError(`Исправьте ошибки: ${result.error_count}. Наведите указатель на выделенное поле или перейдите к нему клавишей Tab.`);
      } else onReady(result);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <section className="transfer-mapping">
    <h4>Перенос в действующие рабочие поля</h4>
    <p>Для каждого значения выберите позицию, показатель и период. Подтвердите соответствие значения этому периоду. Месячную сумму нельзя переносить как недельную без уточнения исходных данных.</p>
    <p>Если нужной позиции или показателя нет, закройте импорт, добавьте их через «Настроить рабочее поле» и повторите проверку. Исключение значения требует явной причины.</p>
    {book.report_type !== matrix.report_type && <p role="alert">Откройте вкладку соответствующего типа отчёта и повторите импорт.</p>}
    {book.warnings.map(w => <p className="reference-warning" key={w}>{w} При сопоставлении укажите правильный год и период.</p>)}
    {error && <p role="alert">{error}</p>}
    <div className="reference-toolbar"><label>Найти позицию или поле <input value={filter} onChange={e => { setFilter(e.target.value); setPage(0); }} /></label>
      <label>Причина исключения несопоставленных полей <input value={excludeReason} onChange={e => setExcludeReason(e.target.value)} /></label>
      <button disabled={busy || excludeReason.trim().length < 3 || !unassigned.length} onClick={excludeUnassigned}>Не переносить несопоставленные поля в фильтре ({unassigned.length})</button></div>
    <div className="reference-scroll"><table className="transfer-table"><thead><tr><th>Источник</th><th>Значение</th><th>Рабочая позиция и показатель</th><th>Период</th><th>Проверка</th></tr></thead><tbody>
      {visible.slice(page * 30, (page + 1) * 30).map(s => {
        const c = choice(s);
        const hint = issues[s.key] ?? (!c.skip && (!c.row || !c.period || !c.confirmed) ? "Выберите рабочий показатель и период; подтвердите, что значение относится именно к ним" : c.skip && c.reason.trim().length < 3 ? "Укажите причину исключения значения" : "Сопоставление заполнено; выполните проверку");
        const invalid = !!issues[s.key] || (c.skip ? c.reason.trim().length < 3 : !c.row || !c.period || !c.confirmed);
        return <tr key={s.key} className={invalid ? "transfer-invalid" : ""} title={hint}>
          <td><strong>{s.context}</strong><br />{s.label}<br />Исходное: {s.value}</td>
          <td><input aria-label={`Значение ${s.key}`} disabled={busy || c.skip} value={c.quantity} onChange={e => update(s, { quantity: e.target.value, confirmed: false })} /></td>
          <td><select aria-label={`Показатель ${s.key}`} title={hint} aria-invalid={invalid} disabled={busy || c.skip} value={c.row} onChange={e => update(s, { row: e.target.value, confirmed: false })}>
            <option value="">Выберите позицию и показатель</option>{rows.map(r => <option key={r.id} value={r.id}>{Object.values(r.left_values).join(" · ")}</option>)}</select></td>
          <td><select aria-label={`Период ${s.key}`} title={hint} disabled={busy || c.skip} value={c.period} onChange={e => update(s, { period: e.target.value, confirmed: false })}>
            <option value="">Выберите период</option>{matrix.time_columns.map(p => <option key={p.id} value={p.id}>{p.group_label} · {p.label}</option>)}</select></td>
          <td><label><input type="checkbox" disabled={busy || c.skip || !c.row || !c.period} checked={c.confirmed} onChange={e => update(s, { confirmed: e.target.checked })} />Значение и период верны</label>
            <label><input type="checkbox" disabled={busy} checked={c.skip} onChange={e => update(s, { skip: e.target.checked })} />Не переносить</label>
            {c.skip && <input aria-label={`Причина исключения ${s.key}`} disabled={busy} placeholder="Причина исключения" value={c.reason} onChange={e => update(s, { reason: e.target.value })} />}
            {issues[s.key] && <small>{issues[s.key]}</small>}</td>
        </tr>;
      })}
    </tbody></table></div>
    <div className="reference-toolbar"><button disabled={busy || page === 0} onClick={() => setPage(p => p - 1)}>Предыдущие поля</button>
      <span>Поля {visible.length ? page * 30 + 1 : 0}–{Math.min((page + 1) * 30, visible.length)} из {visible.length}</span>
      <button disabled={busy || (page + 1) * 30 >= visible.length} onClick={() => setPage(p => p + 1)}>Следующие поля</button>
      <button disabled={busy || book.report_type !== matrix.report_type} onClick={() => void check()}>{busy ? "Проверка…" : "Проверить сопоставление"}</button></div>
  </section>;
}
