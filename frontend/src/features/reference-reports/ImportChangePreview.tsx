import { useState } from "react";
import type { ImportPreview } from "../../shared/api/application-gateway";
import type { ReportCellValue } from "../../shared/api/report-cell-contract";

const CLASSIFICATION: Record<string, string> = { NEW: "Новое значение", CHANGED: "Изменение", SAME: "Без изменений", SKIPPED: "Не переносится", ERROR: "Ошибка" };
const FIELD_LABELS: Record<string, string> = { product_name: "Изделие", product_designation: "Шифр изделия", factory_name: "Завод", name: "Название", code: "Обозначение", norm: "Входимость", manufacturer: "Изготовитель", suppliers: "Изготовители", contract: "Договорной объём", plans: "Планы", actuals: "Фактический выпуск", header: "Шапка", parent_code: "Родительская позиция" };
function valueText(value: ReportCellValue | null): string {
  if (value === null) return "Нет записи";
  return value.kind === "QUANTITY" ? value.quantity : "Пусто — данные не представлены";
}
function requisiteText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Пусто";
  if (Array.isArray(value)) return value.map(requisiteText).join("; ") || "Пусто";
  if (typeof value === "object") return Object.entries(value).map(([key, item]) => `${FIELD_LABELS[key] ?? key}: ${requisiteText(item)}`).join("; ") || "Пусто";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  return String(value);
}

/** These are reviewed backend changes, never inferred from worksheet numeric cells. */
export function ImportChangePreview({ preview, showValues = true }: { preview: ImportPreview; showValues?: boolean }) {
  const [filter, setFilter] = useState("");
  const [onlyErrors, setOnlyErrors] = useState(false);
  const [page, setPage] = useState(0);
  const changes = showValues ? preview.value_changes ?? [] : [];
  const structural = preview.metadata?.structural_changes ?? [];
  const filtered = changes.filter(change => (!onlyErrors || change.classification === "ERROR") &&
    `${change.source_cell} ${change.target} ${change.period} ${valueText(change.before)} ${valueText(change.after)}`.toLocaleLowerCase("ru").includes(filter.toLocaleLowerCase("ru")));
  const lastPage = Math.max(0, Math.ceil(filtered.length / 50) - 1);
  const currentPage = Math.min(page, lastPage);
  if (!changes.length && !structural.length) return null;
  return <section className="import-change-preview">
    {!!changes.length && <>
      <h4>Значения перед записью</h4>
      <p>Сверьте назначение и период каждого изменения. Пустое значение отличается от подтверждённого нуля.</p>
      {preview.validation_pending && <p role="status">Показан предыдущий результат. Выполните повторную проверку перед подтверждением.</p>}
      <div className="reference-toolbar"><label>Поиск в изменениях<input aria-label="Поиск в изменениях" value={filter} onChange={event => { setFilter(event.target.value); setPage(0); }} /></label>
        <label><input type="checkbox" checked={onlyErrors} onChange={event => { setOnlyErrors(event.target.checked); setPage(0); }} />Только ошибочные значения</label></div>
      <div className="reference-scroll" tabIndex={0} role="region" aria-label="Проверяемые значения импорта"><table className="transfer-table"><thead><tr><th>Источник</th><th>Рабочее поле</th><th>Период</th><th>До импорта</th><th>После импорта</th><th>Результат</th></tr></thead><tbody>
        {filtered.slice(currentPage * 50, (currentPage + 1) * 50).map((change, index) => <tr key={`${change.source_cell}:${index}`} className={change.classification === "ERROR" ? "transfer-invalid" : ""}>
          <td>{change.source_cell}</td><td>{change.target}</td><td>{change.period}</td><td>{valueText(change.before)}</td><td>{valueText(change.after)}</td><td>{CLASSIFICATION[change.classification] ?? "Требует проверки"}</td>
        </tr>)}
      </tbody></table></div>
      <div className="reference-toolbar"><button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Предыдущие изменения</button>
        <span>Значения {filtered.length ? currentPage * 50 + 1 : 0}–{Math.min((currentPage + 1) * 50, filtered.length)} из {filtered.length}</span>
        <button type="button" disabled={currentPage >= lastPage} onClick={() => setPage(currentPage + 1)}>Следующие изменения</button></div>
    </>}
    {!!structural.length && <details open><summary>Изменяемые реквизиты ({structural.length})</summary><div className="reference-scroll"><table className="transfer-table"><thead><tr><th>Источник / реквизит</th><th>До импорта</th><th>После импорта</th></tr></thead><tbody>
      {structural.map((change, index) => <tr key={`${change.source_cell}:${index}`}><td>{change.source_cell}{change.label ? ` · ${change.label}` : ""}</td><td>{requisiteText(change.before)}</td><td>{requisiteText(change.after)}</td></tr>)}
    </tbody></table></div></details>}
  </section>;
}
