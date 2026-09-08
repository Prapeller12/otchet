import { useEffect, useMemo, useState } from "react";
import type { ApplicationGateway, ReferenceWorkbook, ReferenceSheet, ExportResult } from "../../shared/api/application-gateway";
import "./reference-report.css";

function column(n: number): string {
  let label = "";
  while (n > 0) { n--; label = String.fromCharCode(65 + n % 26) + label; n = Math.floor(n / 26); }
  return label;
}
function point(address: string): [number, number] {
  const letters = address.match(/^[A-Z]+/)?.[0] ?? "A";
  return [Array.from(letters).reduce((v, c) => v * 26 + c.charCodeAt(0) - 64, 0), Number(address.slice(letters.length))];
}

export function ReferenceGrid({ sheet, drafts = {}, onEdit }: {
  sheet: ReferenceSheet; drafts?: Record<string, string>; onEdit?: ((address: string, value: string) => void) | undefined;
}) {
  const spans = useMemo(() => {
    const result = new Map<string, [number, number] | null>();
    for (const merge of sheet.merges) {
      const [a, b] = merge.split(":");
      const [x1, y1] = point(a!); const [x2, y2] = point(b ?? a!);
      for (let y = y1; y <= y2; y++) for (let x = x1; x <= x2; x++) result.set(`${column(x)}${y}`, null);
      result.set(a!, [y2-y1+1, x2-x1+1]);
    }
    return result;
  }, [sheet]);
  const [editing, setEditing] = useState<string | null>(null);
  return <div className="reference-scroll"><table className="reference-grid" aria-label={sheet.name}>
    <colgroup>{Array.from({ length: sheet.columns }, (_, x) => <col key={x} style={{ width: [90,60,160,180,80,180,100,110,110][x] ?? 72 }} />)}</colgroup>
    <tbody>{Array.from({ length: sheet.rows }, (_, r) => <tr key={r}>{Array.from({ length: sheet.columns }, (_, c) => {
      const address = `${column(c+1)}${r+1}`;
      const span = spans.get(address);
      if (span === null) return null;
      const cell = sheet.cells[address];
      const editable = onEdit !== undefined && r >= 8 && cell?.kind !== "f" && cell?.kind !== "d";
      return <td key={c} rowSpan={span?.[0]} colSpan={span?.[1]} className={`${r < 8 ? "reference-header" : ""} ${cell?.kind === "f" ? "reference-formula" : ""} ${editable ? "reference-editable" : ""}`}
        title={cell?.kind === "f" ? `Расчёт: ${cell.value}` : editable ? "Двойной щелчок или Enter — ввод" : undefined}
        tabIndex={editable ? 0 : undefined} onDoubleClick={() => editable && setEditing(address)}
        onKeyDown={e => { if (editable && e.key === "Enter") setEditing(address); }}>
        {editing === address && editable ? <input autoFocus aria-label={`Значение ${address}`} defaultValue={drafts[address] ?? cell?.value ?? ""}
          onBlur={e => { onEdit(address, e.currentTarget.value); setEditing(null); }}
          onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); } if (e.key === "Escape") { setEditing(null); } }} />
          : drafts[address] ?? cell?.display ?? ""}
      </td>;
    })}</tr>)}</tbody>
  </table></div>;
}

export function ReferenceReport({ gateway, organizationId, identity, onBack, onDirty }: {
  gateway: ApplicationGateway; organizationId: string; identity: string; onBack: () => void; onDirty: (dirty: boolean) => void;
}) {
  const [book, setBook] = useState<ReferenceWorkbook | null>(null);
  const [sheet, setSheet] = useState(0);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    setBook(null); setError(null); setDrafts({}); setSheet(0);
    gateway.referenceReport?.({ action: "get", organization_id: organizationId, id: identity })
      .then(result => { if (active) setBook(result as ReferenceWorkbook); })
      .catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [gateway, organizationId, identity]);
  const changed = Object.keys(drafts).length > 0;
  useEffect(() => { onDirty(changed || busy); return () => onDirty(false); }, [changed, busy, onDirty]);
  async function save() {
    if (!book || !gateway.referenceReport) return;
    setBusy(true); setError(null);
    try {
      const result = await gateway.referenceReport({ action: "save", organization_id: organizationId, id: identity, revision: book.revision,
        changes: Object.entries(drafts).map(([address,value]) => ({ sheet, address, value })) });
      setBook(result as ReferenceWorkbook); setDrafts({}); setMessage("Изменения сохранены. Формулы пересчитаны.");
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function exportBook() {
    if (!gateway.referenceReport) return;
    setBusy(true); setError(null);
    try {
      const result = await gateway.referenceReport({ action: "export", organization_id: organizationId, id: identity }) as ExportResult;
      if (!result.cancelled) setMessage(`Выгружен файл: ${result.file_name}`);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <section className="reference-report">
    <div className="reference-toolbar"><h2>{book?.file_name ?? "Загрузка отчёта…"}</h2>
      <button disabled={busy || changed} onClick={onBack}>К рабочей форме</button>
      <button disabled={busy || changed || !book} onClick={() => void exportBook()}>Экспорт Excel</button>
      <button disabled={busy || !changed} onClick={() => setDrafts({})}>Отменить изменения</button>
      <button disabled={busy || !changed} onClick={() => void save()}>Сохранить изменения</button>
    </div>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {book?.warnings.map(w => <p key={w} className="reference-warning">{w}</p>)}
    <p>Отчёт по исходному Excel. Двойной щелчок по ячейке — ввод. Серые ячейки рассчитываются автоматически.{changed ? " Есть несохранённые изменения." : ""}</p>
    {book && <><label>Лист <select value={sheet} disabled={changed || busy} onChange={e => setSheet(Number(e.target.value))}>
      {book.sheets.map((s,i) => <option key={i} value={i}>{s.name}</option>)}</select></label>
      <ReferenceGrid key={`${identity}:${sheet}`} sheet={book.sheets[sheet]!} drafts={drafts} onEdit={busy ? undefined : (a,v) => setDrafts(d => ({ ...d, [a]:v }))} /></>}
  </section>;
}
