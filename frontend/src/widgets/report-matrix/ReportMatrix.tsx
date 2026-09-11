import { SubsidiaryControls } from "./SubsidiaryControls";
import { ReferenceTransfer } from "../../features/reference-reports/ReferenceTransfer";
import { MonthlyReportActions } from "./MonthlyReportActions";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import type {
  ApplicationGateway,
  ImportPreview,
  MatrixCellContract,
  ReportMatrixContract,
} from "../../shared/api/application-gateway";
import type { ReportCellCoordinate } from "../../shared/api/report-cell-contract";
import { CATEGORY_LABELS } from "../../features/workspace-settings/PositionFieldsEditor";
import { CellEditor } from "./CellEditor";
import { ColumnResizeHandle } from "./ColumnResizeHandle";
import { UiIcon } from "../../shared/ui/UiIcon";
import { inputValue, parseCellDraft, sumCellValues } from "./cell-value";
import {
  moveAfterEnter,
  moveByArrow,
  moveByTab,
  type ArrowKey,
  type MatrixPosition,
} from "./matrix-navigation";
import { ReportCellView } from "./ReportCellView";
import "./matrix.css";

type ReportMatrixProps = {
  gateway: ApplicationGateway;
  matrix: ReportMatrixContract;
  onChange(matrix: ReportMatrixContract): void;
  onStatusChange(status: string): void;
};

type EditingCell = MatrixPosition & { draft: string };

function cellKey(cell: MatrixCellContract): string {
  return JSON.stringify(cell.coordinate);
}
function updateCell(
  matrix: ReportMatrixContract,
  position: MatrixPosition,
  update: (cell: MatrixCellContract) => MatrixCellContract,
): ReportMatrixContract {
  return {
    ...matrix,
    rows: matrix.rows.map((row, rowIndex) =>
      rowIndex === position.row
        ? {
            ...row,
            cells: row.cells.map((cell, columnIndex) =>
              columnIndex === position.column ? update(cell) : cell,
            ),
          }
        : row,
    ),
  };
}

function updateCells(
  matrix: ReportMatrixContract,
  keys: ReadonlySet<string>,
  update: (cell: MatrixCellContract) => MatrixCellContract,
): ReportMatrixContract {
  return {
    ...matrix,
    rows: matrix.rows.map((row) => ({
      ...row,
      cells: row.cells.map((cell) => (keys.has(cellKey(cell)) ? update(cell) : cell)),
    })),
  };
}

function coordinateKey(coordinate: ReportCellCoordinate): string {
  return JSON.stringify(coordinate);
}

function groupSpans(matrix: ReportMatrixContract): Map<number, number> {
  const spans = new Map<number, number>();
  let start = 0;
  while (start < matrix.rows.length) {
    const groupId = matrix.rows[start]?.group_id;
    let end = start + 1;
    while (end < matrix.rows.length && matrix.rows[end]?.group_id === groupId) end += 1;
    spans.set(start, end - start);
    start = end;
  }
  return spans;
}

function headerGroups(matrix: ReportMatrixContract) {
  const groups: Array<{ label: string; span: number }> = [];
  for (const column of matrix.time_columns) {
    const last = groups.at(-1);
    if (last?.label === column.group_label) last.span += 1;
    else groups.push({ label: column.group_label, span: 1 });
  }
  return groups;
}

function stickyOffsets(matrix: ReportMatrixContract): number[] {
  let offset = 0;
  return matrix.left_columns.map((column) => {
    const current = offset;
    offset += column.width;
    return current;
  });
}

function monthName(month: string): string {
  const date = new Date(`${month}-01T12:00:00`);
  return Number.isNaN(date.getTime()) ? month : new Intl.DateTimeFormat("ru", { month: "long" }).format(date);
}

function newIdempotencyKey(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function ReportMatrix({
  gateway,
  matrix,
  onChange,
  onStatusChange,
}: ReportMatrixProps) {
  const [active, setActive] = useState<MatrixPosition>({ row: 0, column: 0 });
  const [editing, setEditing] = useState<EditingCell | null>(null);
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(() => new Set());
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [excelBusy, setExcelBusy] = useState<"import" | "commit" | "export" | null>(null);
  const [excelError, setExcelError] = useState<string | null>(null);
  const [excelMessage, setExcelMessage] = useState<string | null>(null);
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null);
  const [widths, setWidths] = useState<Record<string, number>>(matrix.presentation?.widths ?? {});
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState(matrix.title);
  const [presentationBusy, setPresentationBusy] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const previewSequence = useRef(0);
  const latestMatrix = useRef(matrix);
  latestMatrix.current = matrix;
  const presentationQueue = useRef(Promise.resolve());
  const monthLabels = [...new Set(matrix.time_columns.map((column) => column.group_label))];
  const [expandedMonths, setExpandedMonths] = useState<Set<string>>(() => {
    const current = `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, "0")}`;
    return new Set([matrix.time_columns.find((column) => column.group_label === current)?.group_label ?? matrix.time_columns[0]?.group_label ?? ""]);
  });
  const [subsidiaryMonth, setSubsidiaryMonth] = useState([...expandedMonths][0] ?? "");
  const [stockWeeks, setStockWeeks] = useState<Record<string, string>>({});
  const visibleIndices = matrix.time_columns.flatMap((column, index) => expandedMonths.has(column.group_label) ? [index] : []);
  const visibleSet = new Set(visibleIndices);
  const leftColumns = matrix.left_columns.map((column) => ({ ...column, width: widths[column.id] ?? column.width }));
  const timeColumns = matrix.time_columns.map((column) => ({ ...column, width: Math.max(matrix.subsidiary && column.kind !== "USED" ? 110 : 64, widths[column.id] ?? (matrix.subsidiary && column.kind !== "USED" ? 110 : 64)) }));
  const visibleMatrix = { ...matrix, left_columns: leftColumns, time_columns: timeColumns.filter((_, index) => visibleSet.has(index)), rows: matrix.rows.map((row) => ({ ...row, cells: row.cells.filter((_, index) => visibleSet.has(index)) })) };
  const query = { report_type: matrix.report_type, organization_id: matrix.organization_id, ...(matrix.year ? { year: matrix.year } : {}) };

  const spans = useMemo(() => groupSpans(matrix), [matrix]);
  const groups = headerGroups(visibleMatrix);
  const offsets = stickyOffsets(visibleMatrix);

  useEffect(() => () => { previewSequence.current += 1; }, []);

  function sharedColumn(column: number) {
    return matrix.subsidiary && ["OPENING", "STOCK", "VARIANCE"].includes(matrix.time_columns[column]?.kind ?? "");
  }
  function groupStart(row: number) {
    while (row > 0 && matrix.rows[row - 1]?.group_id === matrix.rows[row]?.group_id) row--;
    return row;
  }
  function navigate(position: MatrixPosition, move: (source: ReportMatrixContract, position: MatrixPosition) => MatrixPosition) {
    const projected = { row: position.row, column: Math.max(0, visibleIndices.indexOf(position.column)) };
    let next = move(visibleMatrix, projected);
    const column = visibleIndices[next.column] ?? 0;
    if (sharedColumn(column)) {
      // A merged cell is a single keyboard target. Down jumps to the next detail.
      if (next.row > position.row && groupStart(next.row) === groupStart(position.row)) {
        const end = groupStart(position.row) + (spans.get(groupStart(position.row)) ?? 1);
        next = { ...next, row: end < matrix.rows.length ? end : groupStart(position.row) };
      } else next = { ...next, row: groupStart(next.row) };
    }
    setActive({ row: next.row, column });
  }

  function toggleMonth(month: string) {
    if (editing) return;
    const next = new Set(expandedMonths);
    if (next.has(month)) next.delete(month); else next.add(month);
    setExpandedMonths(next);
    if (!next.has(matrix.time_columns[active.column]?.group_label ?? "")) {
      setActive({ row: active.row, column: Math.max(0, matrix.time_columns.findIndex((column) => next.has(column.group_label))) });
    }
  }

  function persistWidths(next: Record<string, number>) {
    if (!gateway.saveReportPresentation) return;
    presentationQueue.current = presentationQueue.current.then(async () => {
      try { await gateway.saveReportPresentation!({ report_type: matrix.report_type, organization_id: matrix.organization_id, widths: next }); }
      catch (error) { setSaveError(error instanceof Error ? error.message : "Ширина не сохранена. Повторите изменение."); }
    });
  }

  function resizeHandle(id: string, label: string, width: number) {
    return <ColumnResizeHandle label={label} width={width} minimum={matrix.time_columns.some(column => column.id === id) ? 64 : 48}
      onResize={(value) => setWidths((current) => ({ ...current, [id]: value }))}
      onCommit={(value) => persistWidths({ ...widths, [id]: value })} />;
  }

  async function renameReport() {
    if (!titleDraft.trim() || !gateway.saveReportPresentation) return;
    setPresentationBusy(true);
    try {
      const result = await gateway.saveReportPresentation({ report_type: matrix.report_type, organization_id: matrix.organization_id, title: titleDraft.trim() });
      onChange({ ...latestMatrix.current, title: result.title ?? titleDraft.trim() });
      setRenaming(false);
    } catch (error) { setSaveError(error instanceof Error ? error.message : "Название не сохранено"); }
    finally { setPresentationBusy(false); }
  }

  async function previewCalculations(next: ReportMatrixContract) {
    const sequence = ++previewSequence.current;
    if (gateway.mode !== "pywebview") return;
    setPreviewBusy(true);
    try {
      const result = await gateway.getReportMatrix({ ...query, preview_changes: next.rows.flatMap((row) => row.cells.filter((cell) => cell.state.access === "editable" && cell.state.persistence === "dirty").map((cell) => ({ coordinate: cell.coordinate, value: cell.value }))) });
      if (sequence !== previewSequence.current) return;
      const calculated = new Map(result.rows.flatMap((row) => row.cells.filter((cell) => cell.state.access === "calculated").map((cell) => [cellKey(cell), cell] as const)));
      const current = latestMatrix.current;
      onChange({ ...current, rows: current.rows.map((row) => ({ ...row, stock_by_week: result.rows.find(r => r.id === row.id)?.stock_by_week ?? {}, cells: row.cells.map((cell) => calculated.get(cellKey(cell)) ?? cell) })) });
    } catch (error) {
      if (sequence === previewSequence.current) setSaveError(error instanceof Error ? error.message : "Не удалось пересчитать форму");
    } finally { if (sequence === previewSequence.current) setPreviewBusy(false); }
  }

  useEffect(() => {
    const row = matrix.rows[active.row];
    const column = matrix.time_columns[active.column];
    if (row === undefined || column === undefined) {
      onStatusChange("Нет доступных строк");
      return;
    }
    const identifiers = matrix.left_columns
      .map((item) => row.left_values[item.id])
      .filter((value): value is string => Boolean(value && value !== "—"));
    onStatusChange(`${identifiers.join(" · ")} · ${column.label}${row.cells[active.column]?.issue ? " · " + row.cells[active.column]!.issue!.message : ""}`);
  }, [active, matrix, onStatusChange]);

  function beginEdit(position: MatrixPosition): void {
    const cell = matrix.rows[position.row]?.cells[position.column];
    if (cell?.state.access !== "editable" || saving || presentationBusy || excelBusy !== null) return;
    setActive(position);
    setEditing({ ...position, draft: inputValue(cell.value) });
  }

  function commitEdit(
    move: "enter" | "tab" | "stay",
    backwards = false,
  ): void {
    if (editing === null) return;
    const parsed = parseCellDraft(editing.draft);
    const position = { row: editing.row, column: editing.column };

    if (!parsed.valid) {
      onChange(
        updateCell(matrix, position, (cell) => ({
          ...cell,
          state: { access: cell.state.access, persistence: "error" },
          issue: { code: "INVALID_DECIMAL", message: parsed.message },
        })),
      );
      setEditing(null);
      return;
    }

    const nextMatrix = updateCell(matrix, position, (cell) => {
      const { issue: _issue, ...withoutIssue } = cell;
      return {
        ...withoutIssue,
        value: parsed.value,
        state: { access: cell.state.access, persistence: "dirty" },
      };
    });
    const changedCell = nextMatrix.rows[position.row]?.cells[position.column];
    if (changedCell !== undefined) {
      setDirtyKeys((current) => new Set(current).add(cellKey(changedCell)));
    }
    onChange(nextMatrix);
    void previewCalculations(nextMatrix);
    setEditing(null);
    setSaveError(null);

    if (move === "enter") navigate(position, moveAfterEnter);
    if (move === "tab") navigate(position, (source, current) => moveByTab(source, current, backwards));
  }

  function handleCellKeyDown(
    event: KeyboardEvent<HTMLButtonElement>,
    position: MatrixPosition,
  ): void {
    if (event.key.startsWith("Arrow")) {
      event.preventDefault();
      navigate(position, (source, current) =>
        moveByArrow(
          current,
          event.key as ArrowKey,
          source.rows.length,
          source.time_columns.length,
        ),
      );
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      navigate(position, (source, current) => moveByTab(source, current, event.shiftKey));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      beginEdit(position);
    }
  }

  async function saveChanges(): Promise<void> {
    if (dirtyKeys.size === 0 || saving) return;
    previewSequence.current += 1;
    setPreviewBusy(false);
    const keysAtStart = new Set(dirtyKeys);
    const changes = matrix.rows.flatMap((row) =>
      row.cells
        .filter((cell) => keysAtStart.has(cellKey(cell)))
        .map((cell) => ({ coordinate: cell.coordinate, value: cell.value })),
    );
    const savingMatrix = updateCells(matrix, keysAtStart, (cell) => ({
      ...cell,
      state: { access: cell.state.access, persistence: "saving" },
    }));
    onChange(savingMatrix);
    setSaving(true);
    setSaveError(null);

    try {
      const result = await gateway.saveReportCells({
        ...query,
        report_type: matrix.report_type,
        organization_id: matrix.organization_id,
        base_revision: matrix.matrix_revision,
        idempotency_key: newIdempotencyKey(),
        changes,
      });
      const savedCells = new Map(
        result.cells.map((cell) => [coordinateKey(cell.coordinate), cell]),
      );
      const savedMatrix = updateCells(savingMatrix, keysAtStart, (cell) => {
        const saved = savedCells.get(coordinateKey(cell.coordinate));
        return saved === undefined
          ? cell
          : { ...cell, value: saved.value, state: saved.state };
      });
      onChange({ ...savedMatrix, matrix_revision: result.matrix_revision });
      setDirtyKeys((current) => {
        const next = new Set(current);
        keysAtStart.forEach((key) => next.delete(key));
        return next;
      });
      if (matrix.rows.some((row) => row.cells.some((cell) => cell.formula))) {
        try {
          onChange(await gateway.getReportMatrix(query));
        } catch {
          setSaveError("Данные сохранены, но не удалось обновить расчёты. Перезагрузите отчёт.");
        }
      }
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : "Сохранение не выполнено";
      onChange(
        updateCells(savingMatrix, keysAtStart, (cell) => ({
          ...cell,
          state: { access: cell.state.access, persistence: "error" },
          issue: { code: "SAVE_FAILED", message },
        })),
      );
      setSaveError(message);
    } finally {
      setSaving(false);
    }
  }

  async function startImport(): Promise<void> {
    setExcelBusy("import");
    setExcelError(null);
    setExcelMessage(null);
    try {
      const preview = await gateway.validateImport({
        ...query,
        report_type: matrix.report_type,
        organization_id: matrix.organization_id,
      });
      if (!preview.cancelled) setImportPreview(preview);
    } catch (reason: unknown) {
      setExcelError(reason instanceof Error ? reason.message : "Excel-файл не проверен");
    } finally {
      setExcelBusy(null);
    }
  }

  async function confirmImport(): Promise<void> {
    if (importPreview?.batch_id === undefined) return;
    setExcelBusy("commit");
    setExcelError(null);
    try {
      const result = await gateway.commitImport({ batch_id: importPreview.batch_id, ...(matrix.year ? { year: matrix.year } : {}) });
      if (result.reference_workbook_id) {
        window.dispatchEvent(new CustomEvent("reference-report-imported", { detail: { id: result.reference_workbook_id } }));
        setImportPreview(null);
        return;
      }
      const refreshed = await gateway.getReportMatrix({
        ...query,
        report_type: matrix.report_type,
        organization_id: matrix.organization_id,
      });
      onChange(refreshed);
      setDirtyKeys(new Set());
      setImportPreview(null);
      setExcelMessage(
        `Импорт завершён: записано ${result.imported_count}, без изменений ${result.same_count}.`,
      );
    } catch (reason: unknown) {
      setExcelError(reason instanceof Error ? reason.message : "Импорт не завершён");
    } finally {
      setExcelBusy(null);
    }
  }

  async function removeSupplier(workspaceId: string, supplierId: string) {
    if (!window.confirm("Убрать производителя из рабочего поля? Сохранённая история останется доступна.")) return;
    setPresentationBusy(true); setSaveError(null);
    try {
      const layout = await gateway.getReportLayout({ report_type: matrix.report_type, organization_id: matrix.organization_id });
      const rows = layout.rows.map(row => row.id === workspaceId && row.configuration?.subsidiary ? { ...row, configuration: { ...row.configuration, subsidiary: { ...row.configuration.subsidiary, suppliers: row.configuration.subsidiary.suppliers.map(s => s.id === supplierId ? { ...s, archived: true } : s) } } } : row);
      await gateway.saveReportLayout({ report_type: matrix.report_type, organization_id: matrix.organization_id, rows });
      onChange(await gateway.getReportMatrix(query));
    } catch (e) { setSaveError(e instanceof Error ? e.message : String(e)); }
    finally { setPresentationBusy(false); }
  }

  async function exportExcel(): Promise<void> {
    setExcelBusy("export");
    setExcelError(null);
    setExcelMessage(null);
    try {
      const result = await gateway.exportReport({
        ...query,
        report_type: matrix.report_type,
        organization_id: matrix.organization_id,
        visible_months: [...expandedMonths],
        stock_weeks: stockWeeks,
      });
      if (!result.cancelled) {
        setExcelMessage(
          `Excel сохранён: ${result.file_name ?? "файл"} (${result.exported_cell_count ?? 0} полей ввода).`,
        );
      }
    } catch (reason: unknown) {
      setExcelError(reason instanceof Error ? reason.message : "Excel-файл не создан");
    } finally {
      setExcelBusy(null);
    }
  }

  const importReason = matrix.capabilities.import.enabled
    ? undefined
    : matrix.capabilities.import.reason;
  const exportReason = matrix.capabilities.export.enabled
    ? undefined
    : matrix.capabilities.export.reason;

  return (
    <section className="report-workspace" aria-labelledby="matrix-title">
      <div className="matrix-toolbar">
        <div>
          <div className="title-line">
            {renaming ? <form className="rename-report" onSubmit={(event) => { event.preventDefault(); void renameReport(); }}>
              <input aria-label="Название отчёта" autoFocus maxLength={200} value={titleDraft} onChange={(event) => setTitleDraft(event.target.value)} disabled={presentationBusy} />
              <button className="button primary" disabled={presentationBusy || !titleDraft.trim()}>Применить название</button>
              <button className="button secondary" type="button" disabled={presentationBusy} onClick={() => setRenaming(false)}>Отмена</button>
            </form> : <h2 id="matrix-title">{matrix.title}</h2>}
            {!renaming && gateway.saveReportPresentation && <button type="button" className="mini-button" aria-label="Переименовать отчёт" onClick={() => { setTitleDraft(matrix.title); setRenaming(true); }}><UiIcon name="edit" /> Переименовать отчёт</button>}
          </div>
          {previewBusy && <p role="status">Пересчёт…</p>}
        </div>
        <div className="toolbar-actions">
          <button
            type="button"
            className="button secondary"
            disabled={
              !matrix.capabilities.import.enabled || excelBusy !== null || dirtyKeys.size > 0
            }
            title={importReason}
            onClick={() => void startImport()}
          >
            {excelBusy === "import" ? "Проверка…" : "Импорт Excel"}
            <UiIcon name="import" />
          </button>
          <button
            type="button"
            className="button secondary"
            disabled={
              !matrix.capabilities.export.enabled || excelBusy !== null || dirtyKeys.size > 0
            }
            title={exportReason}
            onClick={() => void exportExcel()}
          >
            {excelBusy === "export" ? "Выгрузка…" : "Экспорт Excel"}
            <UiIcon name="export" />
          </button>
          <button
            type="button"
            className="button primary"
            disabled={dirtyKeys.size === 0 || saving || !matrix.capabilities.save.enabled}
            onClick={() => void saveChanges()}
          >
            {saving ? "Сохранение…" : `Сохранить${dirtyKeys.size > 0 ? ` (${dirtyKeys.size})` : ""}`}
            <UiIcon name="save" />
          </button>
        </div>
      </div>

      {matrix.subsidiary && <SubsidiaryControls matrix={matrix} gateway={gateway} onBusy={setPresentationBusy} blocked={dirtyKeys.size > 0 || editing !== null || saving || previewBusy || excelBusy !== null} onChange={onChange} month={subsidiaryMonth}
        onMonth={month => { setSubsidiaryMonth(month); setExpandedMonths(new Set([month])); }} week={stockWeeks[subsidiaryMonth] ?? ""} onWeek={week => setStockWeeks(current => ({ ...current, [subsidiaryMonth]: week }))} />}
      {!!matrix.legacy_cells?.length && <details className="legacy-facts"><summary>Прежние данные «Поставка» — {matrix.legacy_cells.length} ячеек (не включены в расход)</summary><p>Значения сохранены в исходном смысле. Для переноса в новую структуру используйте проверку импорта.</p><table><thead><tr><th>Позиция / ID</th><th>Показатель</th><th>Период</th><th>Значение</th></tr></thead><tbody>{matrix.legacy_cells.map((cell, i) => <tr key={i}><td>{cell.coordinate.component_id ?? cell.coordinate.product_id}</td><td>{cell.coordinate.metric_code}</td><td>{cell.coordinate.period_start}</td><td>{inputValue(cell.value)}</td></tr>)}</tbody></table></details>}
      {matrix.calendar_notice && <p role="note">{matrix.calendar_notice}</p>}
      <MonthlyReportActions weeks={stockWeeks} gateway={gateway} query={query} revision={matrix.matrix_revision} title={matrix.title} blocked={dirtyKeys.size > 0 || editing !== null || saving || previewBusy || presentationBusy || excelBusy !== null} />
      <nav className="month-controls" aria-label="Месяцы отчёта">
        <button type="button" disabled={editing !== null} onClick={() => {
          const next = { ...widths, ...Object.fromEntries(matrix.time_columns.map(column => [column.id, matrix.subsidiary && column.kind !== "USED" ? 110 : 64])) };
          setWidths(next); persistWidths(next);
        }}>Компактные столбцы: 5 цифр</button>
        {matrix.year && <strong>{matrix.year}</strong>}
        {monthLabels.map((month) => <button key={month} type="button" aria-expanded={expandedMonths.has(month)} disabled={editing !== null}
          onClick={() => toggleMonth(month)}>{expandedMonths.has(month) ? "▾" : "▸"} {monthName(month)}</button>)}
        <button type="button" disabled={editing !== null} onClick={() => setExpandedMonths(new Set(monthLabels))}>Раскрыть все</button>
        <button type="button" disabled={editing !== null} onClick={() => setExpandedMonths(new Set())}>Свернуть все</button>
      </nav>

      {saveError !== null && (
        <div className="save-error" role="alert">
          {saveError}
        </div>
      )}

      {excelError !== null && (
        <div className="save-error" role="alert">{excelError}</div>
      )}
      {excelMessage !== null && (
        <div className="excel-success" role="status">{excelMessage}</div>
      )}

      <div className="matrix-scroll" data-testid="matrix-scroll">
        <table
          className={matrix.subsidiary ? "report-matrix subsidiary-matrix" : "report-matrix"}
          style={{ width: leftColumns.reduce((sum, column) => sum + column.width, 0) + visibleMatrix.time_columns.reduce((sum, column) => sum + column.width, 0), minWidth: 0 }}
          aria-label={matrix.title}
          aria-rowcount={matrix.rows.length + 2}
          aria-colcount={matrix.left_columns.length + visibleMatrix.time_columns.length}
        >
          <colgroup>
            {leftColumns.map((column) => (
              <col key={column.id} style={{ width: column.width }} />
            ))}
            {visibleMatrix.time_columns.map((column) => (
              <col key={column.id} style={{ width: column.width }} />
            ))}
          </colgroup>
          <thead>
            <tr className="matrix-header-group-row">
              {leftColumns.map((column, index) => (
                <th
                  key={column.id}
                  rowSpan={2}
                  className="sticky-left sticky-header"
                  style={{ left: offsets[index] }}
                  scope="col"
                >
                  {column.label}
                  {resizeHandle(column.id, column.label, column.width)}
                </th>
              ))}
              {groups.map((group, index) => (
                <th key={`${group.label}-${index}`} colSpan={group.span} scope="colgroup">
                  <button className="month-heading" type="button" disabled={editing !== null} onClick={() => toggleMonth(group.label)}>▾ {monthName(group.label)}</button>
                </th>
              ))}
            </tr>
            <tr className="matrix-header-leaf-row">
              {visibleMatrix.time_columns.map((column) => (
                <th key={column.id} scope="col">{matrix.subsidiary && column.kind === "USED" ? <button type="button" className="week-heading" title="Показать остаток на конец этой недели" aria-pressed={(stockWeeks[column.group_label] ?? matrix.time_columns.filter(c => c.group_label === column.group_label && c.kind === "USED").at(-1)?.id) === column.id} onClick={() => { setSubsidiaryMonth(column.group_label); setStockWeeks(current => ({ ...current, [column.group_label]: column.id })); }}>Расход<br />{column.label}</button> : column.label}{resizeHandle(column.id, `${column.group_label} ${column.label}`, column.width)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.rows.map((row, rowIndex) => (
              <tr key={row.id}>
                {spans.has(rowIndex) && matrix.left_columns[0] !== undefined && (
                  <th
                    rowSpan={spans.get(rowIndex)}
                    className="sticky-left matrix-group-cell"
                    style={{ left: offsets[0], width: leftColumns[0]?.width }}
                    scope="rowgroup"
                  >
                    {row.left_values[matrix.left_columns[0].id]}
                  </th>
                )}
                {leftColumns.slice(1).map((column, leftIndex) => column.shared && !spans.has(rowIndex) ? null : (
                  <th
                    key={column.id}
                    rowSpan={column.shared ? spans.get(rowIndex) : undefined}
                    className="sticky-left matrix-indicator-cell"
                    style={{
                      left: offsets[leftIndex + 1],
                      width: column.width,
                    }}
                    scope="row"
                  >
                    <span>{row.left_values[column.id]}</span>
                    {matrix.subsidiary && column.id === "party" && !row.archived && row.workspace_id && row.supplier_id && <button type="button" className="mini-button supplier-remove" title="Убрать производителя" aria-label="Убрать производителя" disabled={dirtyKeys.size > 0 || editing !== null || saving || previewBusy || presentationBusy || excelBusy !== null} onClick={() => void removeSupplier(row.workspace_id!, row.supplier_id!)}>×</button>}
                    {(matrix.subsidiary ? column.id === "position" : leftIndex === 0) && row.category && row.category !== "UNSPECIFIED" && (
                      <small className="position-category">{CATEGORY_LABELS[row.category] ?? row.category}</small>
                    )}
                    {(matrix.subsidiary ? column.id === "position" : leftIndex === 0) && row.image && (
                      <img className="matrix-position-image" src={row.image} alt={`Изображение: ${row.group_label}`} />
                    )}
                    {leftIndex === matrix.left_columns.length - 2 &&
                      row.indicator_detail != null && (
                        <span className="indicator-detail">
                          <span>{row.indicator_detail.label}</span>
                          {row.indicator_detail.kind === "SUM" && (
                            <strong>
                              {sumCellValues(row.cells.map((cell) => cell.value)) ?? "—"}
                            </strong>
                          )}
                        </span>
                      )}
                  </th>
                ))}
                {row.cells.map((cell, columnIndex) => {
                  if (!visibleSet.has(columnIndex)) return null;
                  const shared = sharedColumn(columnIndex);
                  if (shared && !spans.has(rowIndex)) return null;
                  const position = { row: rowIndex, column: columnIndex };
                  const isEditing =
                    editing?.row === rowIndex && editing.column === columnIndex;
                  return (
                    <td
                      key={cell.column_id}
                      rowSpan={shared ? spans.get(rowIndex) : undefined}
                      data-shared={shared ? "detail" : undefined}
                      className={`${isEditing ? "matrix-value-cell is-editing" : "matrix-value-cell"} ${cell.tone === "deficit" ? "is-deficit" : ""}`}
                      style={{ width: timeColumns[columnIndex]?.width }}
                    >
                      {isEditing ? (
                        <CellEditor
                          value={editing.draft}
                          label={`Редактирование: ${row.left_values.indicator ?? "ячейка"}`}
                          onChange={(draft) => setEditing({ ...editing, draft })}
                          onCommit={commitEdit}
                          onCancel={() => setEditing(null)}
                        />
                      ) : (
                        <ReportCellView
                          cell={matrix.subsidiary && matrix.time_columns[columnIndex]?.kind === "STOCK" && row.stock_by_week?.[stockWeeks[cell.column_id.slice(0, 7)] ?? ""] ? { ...cell, value: row.stock_by_week[stockWeeks[cell.column_id.slice(0, 7)]!]! } : cell}
                          position={position}
                          active={active.row === rowIndex && active.column === columnIndex}
                          onActivate={setActive}
                          onEdit={beginEdit}
                          onKeyDown={handleCellKeyDown}
                        />
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {importPreview !== null && (
        <div className="excel-dialog-backdrop" role="presentation">
          <section
            className={importPreview.reference_workbook ? "excel-dialog excel-transfer-dialog" : "excel-dialog"}
            role="dialog"
            aria-modal="true"
            aria-labelledby="excel-preview-title"
          >
            <h3 id="excel-preview-title">Проверка импорта Excel</h3>
            <p className="excel-file-name">{importPreview.file_name}</p>
            {importPreview.reference_workbook && <ReferenceTransfer
              book={importPreview.reference_workbook} matrix={matrix} gateway={gateway}
              onReady={setImportPreview} />}
            <div className="excel-preview-counts">
              <span><strong>{importPreview.new_count ?? 0}</strong> новых</span>
              <span><strong>{importPreview.changed_count ?? 0}</strong> изменённых</span>
              <span><strong>{importPreview.same_count ?? 0}</strong> без изменений</span>
              <span className={(importPreview.error_count ?? 0) > 0 ? "has-errors" : ""}>
                <strong>{importPreview.error_count ?? 0}</strong> ошибок
              </span>
            </div>
            {importPreview.already_imported && (
              <p className="excel-preview-note">
                Этот файл уже был импортирован. Повторная запись не требуется.
              </p>
            )}
            {(importPreview.issues?.length ?? 0) > 0 && (
              <ul className="excel-issues">
                {importPreview.issues?.slice(0, 20).map((issue, index) => (
                  <li key={`${issue.source_cell ?? "book"}-${issue.code}-${index}`}>
                    <strong>{issue.source_cell ?? "Книга"}:</strong> {issue.message}
                  </li>
                ))}
              </ul>
            )}
            <div className="excel-dialog-actions">
              <button
                className="button secondary"
                type="button"
                disabled={excelBusy === "commit"}
                onClick={() => setImportPreview(null)}
              >
                Закрыть
              </button>
              <button
                className="button primary"
                type="button"
                disabled={
                  !!importPreview.reference_workbook ||
                  excelBusy === "commit" ||
                  (importPreview.error_count ?? 0) > 0 ||
                  importPreview.already_imported
                }
                onClick={() => void confirmImport()}
              >
                {excelBusy === "commit" ? "Импорт…" : "Подтвердить импорт"}
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
