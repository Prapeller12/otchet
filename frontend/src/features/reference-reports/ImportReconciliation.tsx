import { useState } from "react";
import type { ImportReconciliation as ReconciliationRow } from "../../shared/api/application-gateway";
const STATUS = { MATCH: "Совпадает", MISMATCH: "Расхождение", UNVERIFIED: "Не сверено" };
export function ImportReconciliation({ rows }: { rows: ReconciliationRow[] }) {
  const [page, setPage] = useState(0);
  const lastPage = Math.max(0, Math.ceil(rows.length / 50) - 1);
  const currentPage = Math.min(page, lastPage);
  if (!rows.length) return null;
  return <details><summary>Сверка контрольных итогов ({rows.length})</summary>
    <p>Значения исходного файла сравниваются с расчётом программы. Отметка «Не сверено» не подтверждает совпадение.</p>
    <div className="reference-scroll"><table className="transfer-table"><thead><tr><th>Источник</th><th>Период и единица</th><th>Исходный итог</th><th>Расчёт программы</th><th>Результат</th><th>Причина и округление</th></tr></thead><tbody>
      {rows.slice(currentPage * 50, (currentPage + 1) * 50).map((row, index) => <tr key={`${row.source_cell}:${index}`} className={row.status === "MISMATCH" ? "transfer-invalid" : ""}>
        <td>{row.source_cell || `${row.sheet} · ${row.address}`}</td><td>{typeof row.period === "string" ? row.period : row.period ? `${row.period.start} — ${row.period.end}` : "Период не определён"}{row.unit ? ` · ${row.unit}` : ""}</td>
        <td>{row.source_value ?? "Нет значения"}</td><td>{row.program_value ?? "Не рассчитано"}</td><td>{STATUS[row.status]}</td><td>{row.reason || "Причина не передана. Повторите проверку."}<br />Округление: {row.rounding || "Не задано"}</td>
      </tr>)}
    </tbody></table></div>
    {rows.length > 50 && <div className="reference-toolbar"><button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Предыдущие итоги</button><span>{currentPage * 50 + 1}–{Math.min((currentPage + 1) * 50, rows.length)} из {rows.length}</span><button type="button" disabled={currentPage >= lastPage} onClick={() => setPage(currentPage + 1)}>Следующие итоги</button></div>}
  </details>;
}
