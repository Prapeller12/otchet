import type { ClipboardEventHandler, ReactNode } from "react";
import type { MatrixCellContract, MatrixRowContract, ReportMatrixContract } from "../../shared/api/application-gateway";
import type { ReportCellValue } from "../../shared/api/report-cell-contract";
import { displayValue } from "./cell-value";
import "./source-matrix.css";

type SourceMatrixTableProps = {
  matrix: ReportMatrixContract;
  summaryMonth: string;
  stockWeek: string;
  visibleIndices: number[];
  /** Returns the complete td, preserving the shared matrix editor and coordinates. */
  renderValueCell(rowIndex: number, columnIndex: number): ReactNode;
  leftWidths?: Record<string, number>;
  onPaste?: ClipboardEventHandler<HTMLTableElement>;
  onSelectStockWeek?(week: string): void;
  renderResizeHandle?(columnId: string, label: string, width: number): ReactNode;
  onRemoveSupplier?(workspaceId: string, supplierId: string): void;
  blocked?: boolean;
};

function groupSpans(rows: MatrixRowContract[]): Map<number, number> {
  const result = new Map<number, number>();
  for (let start = 0; start < rows.length;) {
    let end = start + 1;
    while (end < rows.length && rows[end]!.group_id === rows[start]!.group_id) end++;
    result.set(start, end - start);
    start = end;
  }
  return result;
}

function monthLabel(value: string): string {
  const date = new Date(value + "-01T12:00:00");
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("ru", { month: "long", year: "numeric" }).format(date);
}

function ReadonlyValue({ value, cell }: { value?: ReportCellValue | undefined; cell?: MatrixCellContract | undefined }) {
  const current = value ?? cell?.value;
  return <span className="source-readonly-value" title={cell?.issue?.message ?? cell?.formula}>
    {cell?.issue?.code === "MISSING_INPUT" ? "Заполните" : current ? displayValue(current) : "—"}
    {cell?.issue && <span aria-label={cell.issue.message}> !</span>}
  </span>;
}

export function SourceMatrixTable({ matrix, summaryMonth, stockWeek, visibleIndices, renderValueCell, leftWidths = {}, onPaste, onSelectStockWeek, renderResizeHandle, onRemoveSupplier, blocked = false }: SourceMatrixTableProps) {
  const head = !!matrix.head_site;
  const spans = groupSpans(matrix.rows);
  const fixed = [
    { id: "photo", label: "Условное изображение", width: 82, shared: true },
    { id: "number", label: "№ п/п", width: 58, shared: true },
    { id: "designation", label: "Обозначение", width: 130, shared: true },
    { id: "position", label: "Наименование", width: 170, shared: true },
    { id: "norm", label: "Входимость в изделие (шт.)", width: 84, shared: true },
    { id: "party", label: head ? "Контрагент" : "Производитель", width: 150, shared: false },
  ].map(column => ({ ...column, width: Math.max(48, leftWidths[column.id] ?? column.width) }));
  const offsets = fixed.map((_, index) => fixed.slice(0, index).reduce((sum, item) => sum + item.width, 0));
  const selected = (kind: string) => matrix.time_columns.findIndex(column => column.group_label === summaryMonth && column.kind === kind);
  const stockIndex = selected("STOCK");
  const receivedIndex = selected("RECEIVED");
  const factIndex = selected("FACT");
  const varianceIndex = selected("VARIANCE");
  const dates = visibleIndices.filter(index => head ? ["PLAN", "FACT"].includes(matrix.time_columns[index]?.kind ?? "") : matrix.time_columns[index]?.kind === "USED");
  const months: { key: string; span: number }[] = [];
  for (const index of dates) {
    const key = matrix.time_columns[index]!.group_label;
    const previous = months.at(-1);
    if (previous?.key === key) previous.span++;
    else months.push({ key, span: 1 });
  }
  const mainHeaders = head
    ? ["В наличии на складе", "Всего изготовлено за год", "Изготовлено в текущем месяце"]
    : ["В наличии на складе", "Объём поставок по договору", "Поступило за месяц", "Общий профицит / дефицит ДСЕ"];
  const auxiliary = ["OPENING", ...(head ? ["USED", "VARIANCE"] : [])].map(kind => selected(kind)).filter(index => index >= 0);
  const selectedWeek = matrix.time_columns.find(column => column.id === stockWeek);
  const width = fixed.reduce((sum, item) => sum + item.width, 0) + mainHeaders.length * 115 + dates.reduce((sum, index) => sum + Math.max(64, matrix.time_columns[index]!.width), 0);

  return <>
    <p className="source-matrix-period">Сводные колонки: {monthLabel(summaryMonth)}{!head && stockWeek ? `; остаток на конец недели ${selectedWeek?.label ?? stockWeek}` : ""}.</p>
    <div className="matrix-scroll source-matrix-scroll" data-testid="matrix-scroll">
      <table className="report-matrix subsidiary-matrix source-matrix" aria-label={matrix.title} style={{ width, minWidth: width }} onPaste={onPaste}>
        <colgroup>
          {fixed.map(column => <col key={column.id} style={{ width: column.width }} />)}
          {mainHeaders.map(label => <col key={label} style={{ width: 115 }} />)}
          {dates.map(index => <col key={matrix.time_columns[index]!.id} style={{ width: Math.max(64, matrix.time_columns[index]!.width) }} />)}
        </colgroup>
        <thead>
          <tr className="source-header-months">
            {fixed.map((column, index) => <th key={column.id} rowSpan={2} className="sticky-left" style={{ left: offsets[index] }} scope="col">{column.label}{renderResizeHandle?.(column.id, column.label, column.width)}</th>)}
            {mainHeaders.map(label => <th key={label} rowSpan={2} scope="col">{label}</th>)}
            {months.map(month => <th key={month.key} colSpan={month.span} scope="colgroup">{monthLabel(month.key)}</th>)}
          </tr>
          <tr className="source-header-dates">{dates.map(index => <th key={matrix.time_columns[index]!.id} scope="col">{head ? matrix.time_columns[index]!.label : <button type="button" className="week-heading" aria-pressed={stockWeek === matrix.time_columns[index]!.id} title="Показать остаток на конец этой недели" disabled={blocked} onClick={() => onSelectStockWeek?.(matrix.time_columns[index]!.id)}>Расход<br />{matrix.time_columns[index]!.label}</button>}{renderResizeHandle?.(matrix.time_columns[index]!.id, `${matrix.time_columns[index]!.group_label} ${matrix.time_columns[index]!.label}`, Math.max(64, matrix.time_columns[index]!.width))}</th>)}</tr>
        </thead>
        <tbody>{matrix.rows.map((row, rowIndex) => {
          const span = spans.get(rowIndex);
          const stock = stockIndex >= 0 ? row.cells[stockIndex] : undefined;
          return <tr key={row.id}>
            {fixed.map((column, index) => column.shared && !span ? null : <th key={column.id} rowSpan={column.shared ? span : undefined} scope={column.shared ? "rowgroup" : "row"} className="sticky-left source-identity-cell" style={{ left: offsets[index] }}>
              {column.id === "photo" ? row.image ? <img className="source-position-image" src={row.image} alt={`Изображение: ${row.left_values.position ?? row.group_label}`} /> : <span aria-label="Изображение не задано">—</span> : row.left_values[column.id] || "—"}
              {column.id === "party" && onRemoveSupplier && !row.archived && row.workspace_id && row.supplier_id && <button type="button" className="mini-button supplier-remove" aria-label="Убрать производителя" title="Убрать производителя" disabled={blocked} onClick={() => onRemoveSupplier(row.workspace_id!, row.supplier_id!)}>×</button>}
            </th>)}
            {span && <td rowSpan={span} className="source-calculated" data-shared="detail"><ReadonlyValue value={!head && stockWeek ? row.stock_by_week?.[stockWeek] : undefined} cell={stock} /></td>}
            <td className={head ? "source-calculated" : "source-reference-value"}>{head ? (row as MatrixRowContract & { manufactured_total?: string }).manufactured_total || "—" : row.left_values.contract || "—"}</td>
            {head ? <td className="source-calculated"><ReadonlyValue cell={factIndex >= 0 ? row.cells[factIndex] : undefined} /></td> : receivedIndex >= 0 ? renderValueCell(rowIndex, receivedIndex) : <td>—</td>}
            {!head && span && <td rowSpan={span} className={`source-calculated ${row.cells[varianceIndex]?.tone === "deficit" ? "is-deficit" : ""}`} data-shared="detail"><ReadonlyValue cell={varianceIndex >= 0 ? row.cells[varianceIndex] : undefined} /></td>}
            {dates.map(index => renderValueCell(rowIndex, index))}
          </tr>;
        })}</tbody>
      </table>
    </div>
    <section className="source-calculation-fields" aria-labelledby="source-calculation-title">
      <h3 id="source-calculation-title">Данные для расчёта — {monthLabel(summaryMonth)}</h3>
      <p>Начальный остаток вводится один раз для каждой позиции. {head ? "Использовано = выпуск готовых изделий × входимость; остаток на складе = начальный остаток + факт изготовленного − использовано." : "Остаток на складе = начальный остаток + поступления − расход до конца выбранной недели. Договорной объём задаётся в настройках позиции."}</p>
      <div className="matrix-scroll source-auxiliary-scroll"><table className="report-matrix source-auxiliary-matrix" aria-label="Данные для расчёта" onPaste={onPaste}>
        <thead><tr><th scope="col">№ п/п</th><th scope="col">Наименование</th><th scope="col">{head ? "Контрагент" : "Производитель"}</th>{head && <th scope="col">Объём поставок по договору</th>}{auxiliary.map(index => <th key={index} scope="col">{matrix.time_columns[index]!.label}</th>)}</tr></thead>
        <tbody>{matrix.rows.map((row, rowIndex) => {
          const span = spans.get(rowIndex);
          return <tr key={row.id}>
            {span && <><th rowSpan={span} scope="rowgroup">{row.left_values.number || "—"}</th><th rowSpan={span} scope="rowgroup">{row.left_values.position || row.group_label}</th></>}
            <th scope="row">{row.left_values.party || "—"}</th>{head && <td className="source-reference-value">{row.left_values.contract || "—"}</td>}
            {span && auxiliary.map(index => renderValueCell(rowIndex, index))}
          </tr>;
        })}</tbody>
      </table></div>
    </section>
  </>;
}
