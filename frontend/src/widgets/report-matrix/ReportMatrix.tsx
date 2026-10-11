import { SavedReportPdfDialog } from "./SavedReportPdfDialog";
import { DraftTransitionDialog } from "./DraftTransitionDialog";
import { ProductionHeader, type ProductionHeaderPatch } from "./ProductionHeader";
import { DailyMonthlySummary } from "./DailyMonthlySummary";
import { SourceMatrixTable } from "./SourceMatrixTable";
import "./production-header.css";
import { SubsidiaryControls } from "./SubsidiaryControls";
import { ImportChangePreview } from "../../features/reference-reports/ImportChangePreview";
import { CanonicalSheetReview } from "../../features/reference-reports/CanonicalSheetReview";
import { ReferenceTransfer } from "../../features/reference-reports/ReferenceTransfer";
import { MonthlyReportActions } from "./MonthlyReportActions";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ClipboardEvent,
} from "react";

import type {
  ApplicationGateway,
  ImportPreview,
  ImportMode,
  MatrixCellContract,
  ReportMatrixContract,
  ReportDraftPresentation, SaveReportDraftRequest, SaveReportDraftResponse, DraftNavigation,
} from "../../shared/api/application-gateway";
import type { ReportCellValue, ReportCellCoordinate } from "../../shared/api/report-cell-contract";
import { CATEGORY_LABELS } from "../../features/workspace-settings/PositionFieldsEditor";
import { CellEditor } from "./CellEditor";
import { ColumnResizeHandle } from "./ColumnResizeHandle";
import { UiIcon } from "../../shared/ui/UiIcon";
import { HintValue } from "../../shared/ui/FieldHint";
import { cumulativeRowHint, identityHint, reportCellHint, REPORT_FIELD_HINTS } from "../../shared/config/report-field-hints";
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
  workspaceMode?: "entry" | "report-settings" | "admin";
  matrix: ReportMatrixContract;
  onChange(matrix: ReportMatrixContract): void;
  onStatusChange(status: string): void;
  onNavigationBlockedChange?(blocked: boolean): void;
  onNavigationReady?(navigation: DraftNavigation | null): void;
  initialMonth?: number; startPeriod?: string;
};

type EditingCell = MatrixPosition & { draft: string };
type PasteCell = MatrixPosition & { value: ReportCellValue; label: string };

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
  workspaceMode = "entry",
  matrix,
  onChange,
  onStatusChange,
  onNavigationBlockedChange, onNavigationReady, initialMonth, startPeriod,
}: ReportMatrixProps) {
  const adminMode = workspaceMode === "admin";
  const reportSettings = workspaceMode !== "entry";
  const baselineMatrix = useRef(structuredClone(matrix));
  const headerPatch = useRef<ReportDraftPresentation | null>(null);
  const controlsPatch = useRef<ReportDraftPresentation | null>(null);
  const updateHeaderPatch = useCallback((patch: ReportDraftPresentation | null) => { headerPatch.current = patch; }, []);
  const updateControlsPatch = useCallback((patch: ReportDraftPresentation | null) => { controlsPatch.current = patch; }, []);
  const [resetDraft, setResetDraft] = useState(0);
  const [pendingNavigation, setPendingNavigation] = useState<(() => void) | null>(null);
  const [unknownSave, setUnknownSave] = useState<SaveReportDraftRequest | null>(null);
  const [canRetryUnknown, setCanRetryUnknown] = useState(false);
  const [dataStatus, setDataStatus] = useState("Данные загружены");
  const [signatureStatus, setSignatureStatus] = useState("Подтверждение: статус выбранного периода ниже");
  const [backupStatus, setBackupStatus] = useState("Резервная копия: статус последней записи не получен");
  const headerSave = useRef<(() => Promise<void>) | null>(null);
  const [pdfOptionsOpen, setPdfOptionsOpen] = useState(false);
  const [printing, setPrinting] = useState(false);
  const [verificationRefresh, setVerificationRefresh] = useState(0);
  const [moreOpen, setMoreOpen] = useState(adminMode);
  const controlsSave = useRef<(() => Promise<void>) | null>(null);
  const [active, setActive] = useState<MatrixPosition>({ row: 0, column: 0 });
  const [editing, setEditing] = useState<EditingCell | null>(null);
  const [pastePreview, setPastePreview] = useState<PasteCell[] | null>(null);
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(() => new Set());
  const [search, setSearch] = useState("");
  const [matchIndex, setMatchIndex] = useState(-1);
  const [errorIndex, setErrorIndex] = useState(-1);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [excelBusy, setExcelBusy] = useState<"import" | "commit" | "export" | null>(null);
  const [excelError, setExcelError] = useState<string | null>(null);
  const [excelMessage, setExcelMessage] = useState<string | null>(null);
  const [importMode, setImportMode] = useState<ImportMode>("update");
  const [importReviewed, setImportReviewed] = useState(false);
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null);
  useEffect(() => { setImportReviewed(false); }, [importPreview]);
  const [widths, setWidths] = useState<Record<string, number>>(matrix.presentation?.widths ?? {});
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState(matrix.title);
  const [presentationBusy, setPresentationBusy] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [headerDirty, setHeaderDirty] = useState(false);
  const [controlsDirty, setControlsDirty] = useState(false);
  const previewSequence = useRef(0);
  const latestMatrix = useRef(matrix);
  latestMatrix.current = matrix;
  const presentationQueue = useRef(Promise.resolve());
  const monthLabels = [...new Set(matrix.time_columns.map((column) => column.group_label))].filter(month => !startPeriod || month >= startPeriod.slice(0, 7));
  const [expandedMonths, setExpandedMonths] = useState<Set<string>>(() => {
    const current = `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, "0")}`;
    const preferred = initialMonth && matrix.year ? `${matrix.year}-${String(initialMonth).padStart(2, "0")}` : current;
    return new Set([monthLabels.find(month => month === preferred) ?? monthLabels[0] ?? ""]);
  });
  const [subsidiaryMonth, setSubsidiaryMonth] = useState([...expandedMonths][0] ?? "");
  const [stockWeeks, setStockWeeks] = useState<Record<string, string>>({});
  const temporalIndices = matrix.time_columns.flatMap((column, index) => (matrix.subsidiary ? expandedMonths.has(column.group_label) : column.group_label === subsidiaryMonth) && (!matrix.subsidiary || (matrix.head_site ? ["PLAN", "FACT"] : ["SUPPLIED", "USED"]).includes(column.kind ?? "")) ? [index] : []);
  const summaryIndices = matrix.time_columns.flatMap((column, index) => matrix.subsidiary && column.group_label === subsidiaryMonth && (matrix.head_site ? ["OPENING", "USED", "VARIANCE"] : ["OPENING", "RECEIVED"]).includes(column.kind ?? "") ? [index] : []);
  const visibleIndices = matrix.subsidiary ? [
    ...summaryIndices.filter(index => matrix.time_columns[index]?.kind === "RECEIVED"),
    ...temporalIndices,
    ...summaryIndices.filter(index => matrix.time_columns[index]?.kind !== "RECEIVED"),
  ] : temporalIndices;
  const visibleSet = new Set(visibleIndices);
  const leftColumns = matrix.left_columns.map((column) => ({ ...column, width: widths[column.id] ?? column.width }));
  const timeColumns = matrix.time_columns.map((column) => ({ ...column, width: Math.max(matrix.subsidiary && !["SUPPLIED", "USED"].includes(column.kind ?? "") ? 110 : 64, widths[column.id] ?? (matrix.subsidiary && !["SUPPLIED", "USED"].includes(column.kind ?? "") ? 110 : 64)) }));
  const visibleMatrix = { ...matrix, left_columns: leftColumns, time_columns: visibleIndices.map(index => timeColumns[index]!), rows: matrix.rows.map((row) => ({ ...row, cells: visibleIndices.map(index => row.cells[index]!) })) };
  const query = { report_type: matrix.report_type, organization_id: matrix.organization_id, ...(matrix.year ? { year: matrix.year } : {}) };

  const confirmation = { ...(matrix.year ? { year: matrix.year } : {}), month: Number(subsidiaryMonth.slice(5, 7)) || 1, ...(stockWeeks[subsidiaryMonth] ? { week_start: stockWeeks[subsidiaryMonth] } : {}) };

  const spans = useMemo(() => groupSpans(matrix), [matrix]);
  const groups = headerGroups(visibleMatrix);
  const offsets = stickyOffsets(visibleMatrix);

  const dirty = headerDirty || controlsDirty || dirtyKeys.size > 0 || editing !== null || renaming;
  const busy = saving || excelBusy !== null || presentationBusy || importPreview !== null || pastePreview !== null || printing || pdfOptionsOpen;
  const navigationBlocked = unknownSave !== null || headerDirty || controlsDirty || dirtyKeys.size > 0 || editing !== null || saving || excelBusy !== null || presentationBusy || importPreview !== null || pastePreview !== null || renaming || printing || pdfOptionsOpen;
  useEffect(() => { onNavigationBlockedChange?.(navigationBlocked); }, [navigationBlocked, onNavigationBlockedChange]);
  useEffect(() => () => { onNavigationBlockedChange?.(false); }, [onNavigationBlockedChange]);
  useEffect(() => () => { previewSequence.current += 1; }, []);

  useEffect(() => {
    if (visibleIndices.length && !visibleIndices.includes(active.column)) setActive(previous => ({ ...previous, column: visibleIndices[0]! }));
  }, [visibleIndices.join(","), active.column]);

  async function saveHeader(patch: ProductionHeaderPatch) {
    if (unknownSave || saving || excelBusy !== null || importPreview || pastePreview || printing || renaming) throw new Error("Сначала завершите текущую операцию с отчётом.");
    if (dirtyKeys.size || controlsDirty || editing) throw new Error("Сначала сохраните или отмените количественный черновик. Состав кодов применяется отдельно.");
    if (!gateway.saveReportPresentation) throw new Error("Сохранение шапки недоступно");
    const result = await gateway.saveReportPresentation({ report_type: matrix.report_type, organization_id: matrix.organization_id, expected_revision: matrix.matrix_revision, confirmation, ...patch });
    setSaveError(result.verification_error ? `Сохранено, но не подтверждено: ${result.verification_error.message}` : null);
    setVerificationRefresh(value => value + 1);
    onChange({ ...latestMatrix.current, presentation: result });
    try { const refreshed = await gateway.getReportMatrix(query); baselineMatrix.current = structuredClone(refreshed); latestMatrix.current = refreshed; onChange(refreshed); }
    catch { setSaveError(`${result.verification_error ? `Сохранено, но не подтверждено: ${result.verification_error.message}. ` : "Шапка сохранена. "}Не удалось обновить отчёт. Перезагрузите форму перед следующим изменением.`); }
  }

  function sharedColumn(column: number) {
    return matrix.subsidiary && ["OPENING", "STOCK", "VARIANCE", ...(matrix.head_site ? ["USED"] : [])].includes(matrix.time_columns[column]?.kind ?? "");
  }
  function groupStart(row: number) {
    while (row > 0 && matrix.rows[row - 1]?.group_id === matrix.rows[row]?.group_id) row--;
    return row;
  }
  function navigate(position: MatrixPosition, move: (source: ReportMatrixContract, position: MatrixPosition) => MatrixPosition) {
    const projected = { row: position.row, column: Math.max(0, visibleIndices.indexOf(position.column)) };
    const navigationMatrix = { ...visibleMatrix, rows: visibleMatrix.rows.map((row, rowIndex) => ({ ...row, cells: row.cells.map((cell, columnIndex) => (entryLocked(cell, visibleIndices[columnIndex]!) || (sharedColumn(visibleIndices[columnIndex]!) && groupStart(rowIndex) !== rowIndex)) ? { ...cell, state: { ...cell.state, access: "locked" as const } } : cell) })) };
    let next = move(navigationMatrix, projected);
    const column = visibleIndices[next.column] ?? 0;
    if (sharedColumn(column)) {
      // A merged cell is a single keyboard target. Down jumps to the next detail.
      if (next.row > position.row && groupStart(next.row) === groupStart(position.row)) {
        const end = groupStart(position.row) + (spans.get(groupStart(position.row)) ?? 1);
        next = { ...next, row: end < matrix.rows.length ? end : groupStart(position.row) };
      } else next = { ...next, row: groupStart(next.row) };
    }
    setActive({ row: next.row, column });
    return next.row !== position.row || column !== position.column;
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
    if (!adminMode || !gateway.saveReportPresentation) return;
    if (dirty || unknownSave || busy) { setSaveError("Ширина изменена в текущем окне. Сохраните черновик и повторите настройку ширины для записи."); return; }
    presentationQueue.current = presentationQueue.current.then(async () => {
      setPresentationBusy(true);
      try {
        await gateway.saveReportPresentation!({ report_type: matrix.report_type, organization_id: matrix.organization_id, widths: next });
        const refreshed = await gateway.getReportMatrix(query);
        baselineMatrix.current = structuredClone(refreshed); latestMatrix.current = refreshed; onChange(refreshed);
      }
      catch (error) { setSaveError(error instanceof Error ? error.message : "Ширина не сохранена. Повторите изменение."); }
      finally { setPresentationBusy(false); }
    });
  }

  function resizeHandle(id: string, label: string, width: number) {
    return <ColumnResizeHandle label={label} width={width} minimum={matrix.time_columns.some(column => column.id === id) ? 64 : 48}
      onResize={(value) => setWidths((current) => ({ ...current, [id]: value }))}
      onCommit={(value) => persistWidths({ ...widths, [id]: value })} />;
  }

  async function renameReport() { await saveChanges(); }

  async function previewCalculations(next: ReportMatrixContract) {
    const sequence = ++previewSequence.current;
    if (gateway.mode !== "pywebview") return;
    setPreviewBusy(true);
    try {
      const result = await gateway.getReportMatrix({ ...query, preview_changes: next.rows.flatMap((row) => row.cells.filter((cell) => cell.state.access === "editable" && (cell.state.persistence === "dirty" || (dirtyKeys.has(cellKey(cell)) && cell.state.persistence === "error"))).map((cell) => ({ coordinate: cell.coordinate, value: cell.value }))) });
      if (sequence !== previewSequence.current) return;
      const calculated = new Map(result.rows.flatMap((row) => row.cells.filter((cell) => cell.state.access === "calculated").map((cell) => [cellKey(cell), cell] as const)));
      const current = latestMatrix.current;
      onChange({ ...current, ...(result.daily_summary ? { daily_summary: result.daily_summary } : {}), rows: current.rows.map((row) => ({ ...row, stock_by_week: result.rows.find(r => r.id === row.id)?.stock_by_week ?? {}, cells: row.cells.map((cell) => calculated.get(cellKey(cell)) ?? cell) })) });
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

  function entryLocked(cell: MatrixCellContract, column: number): boolean {
    const metric = cell.coordinate.metric_code ?? "";
    const month = matrix.time_columns[column]?.group_label;
    return (!!month && /^\d{4}-\d{2}$/.test(month) && month !== subsidiaryMonth)
      || (!reportSettings && (cell.state.admin_only ?? (matrix.time_columns[column]?.kind === "PLAN" || metric.split("_").includes("PLAN"))));
  }

  function beginEdit(position: MatrixPosition): void {
    const cell = matrix.rows[position.row]?.cells[position.column];
    if (cell?.state.access !== "editable" || printing || entryLocked(cell, position.column) || renaming || unknownSave !== null || saving || presentationBusy || excelBusy !== null || pastePreview !== null) return;
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
      setSaveError(parsed.message);
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
      const original = baselineMatrix.current.rows[position.row]?.cells[position.column];
      const unchanged = original && JSON.stringify(original.value) === JSON.stringify(changedCell.value);
      setDirtyKeys(current => { const next = new Set(current); if (unchanged) next.delete(cellKey(changedCell)); else next.add(cellKey(changedCell)); return next; });
      if (unchanged) changedCell.state.persistence = "saved";
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
      const moved = navigate(position, (source, current) => moveByTab(source, current, event.shiftKey));
      if (moved) event.preventDefault();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      beginEdit(position);
    }
  }

  function previewPaste(event: ClipboardEvent<HTMLTableElement>): void {
    if (event.target instanceof HTMLInputElement) return;
    event.preventDefault();
    if (unknownSave || saving || presentationBusy || excelBusy !== null || editing !== null || pastePreview !== null) return;
    const text = event.clipboardData.getData("text/plain").replace(/\r\n?/g, "\n");
    if (!text) return;
    const rows = text.replace(/\n$/, "").split("\n").map(row => row.split("\t"));
    const width = rows[0]?.length ?? 0;
    const start = visibleIndices.indexOf(active.column);
    const fail = (message: string) => setSaveError(`Диапазон не вставлен: ${message}`);
    if (start < 0 || !width || rows.some(row => row.length !== width)) {
      fail("выберите ячейку и скопируйте прямоугольный диапазон Excel."); return;
    }
    if (active.row + rows.length > matrix.rows.length || start + width > visibleIndices.length) {
      fail("диапазон выходит за границы видимых строк или дат. Раскройте нужные месяцы."); return;
    }
    if (matrix.subsidiary && width > 1) {
      const inAuxiliary = (index: number) => ["OPENING", ...(matrix.head_site ? ["USED", "VARIANCE"] : [])].includes(matrix.time_columns[index]?.kind ?? "");
      if (visibleIndices.slice(start, start + width).some(index => inAuxiliary(index) !== inAuxiliary(active.column))) {
        fail("диапазон пересекает основную таблицу и отдельные данные для расчёта."); return;
      }
    }
    const pending: PasteCell[] = [];
    const used = new Set<string>();
    for (let rowOffset = 0; rowOffset < rows.length; rowOffset++) {
      for (let columnOffset = 0; columnOffset < width; columnOffset++) {
        const column = visibleIndices[start + columnOffset]!;
        const row = active.row + rowOffset;
        const target = matrix.rows[row]!;
        const cell = target.cells[column];
        if (!cell || cell.state.access !== "editable" || entryLocked(cell, column) || (sharedColumn(column) && groupStart(row) !== row)) {
          fail(`строка ${rowOffset + 1}, столбец ${columnOffset + 1} попадает в расчётную, заблокированную или объединённую ячейку.`); return;
        }
        // Decimal comma from Russian Excel is shown in the preview as an exact decimal.
        const parsed = parseCellDraft(rows[rowOffset]![columnOffset]!.replace(",", "."));
        if (!parsed.valid) { fail(`строка ${rowOffset + 1}, столбец ${columnOffset + 1}: ${parsed.message}`); return; }
        const key = cellKey(cell);
        if (used.has(key)) { fail("диапазон повторно затрагивает объединённую ячейку."); return; }
        used.add(key);
        pending.push({ row, column, value: parsed.value, label: `${Object.values(target.left_values).filter(Boolean).join(" · ")} · ${matrix.time_columns[column]!.label}` });
      }
    }
    setSaveError(null);
    setPastePreview(pending);
  }

  function applyPaste(): void {
    if (!pastePreview) return;
    const values = new Map(pastePreview.map(item => [cellKey(matrix.rows[item.row]!.cells[item.column]!), item.value]));
    const keys = new Set(values.keys());
    const next = updateCells(matrix, keys, (cell) => {
      const { issue: _issue, ...rest } = cell;
      return { ...rest, value: values.get(cellKey(cell))!, state: { access: cell.state.access, persistence: "dirty" } };
    });
    setDirtyKeys(current => {
      const nextKeys = new Set(current);
      for (const item of pastePreview) {
        const cell = next.rows[item.row]!.cells[item.column]!;
        if (JSON.stringify(cell.value) === JSON.stringify(baselineMatrix.current.rows[item.row]?.cells[item.column]?.value)) { nextKeys.delete(cellKey(cell)); cell.state.persistence = "saved"; }
        else nextKeys.add(cellKey(cell));
      }
      return nextKeys;
    });
    onChange(next);
    setPastePreview(null);
    void previewCalculations(next);
  }

  function discardDraft() {
    previewSequence.current++;
    onChange(structuredClone(baselineMatrix.current));
    latestMatrix.current = structuredClone(baselineMatrix.current);
    setDirtyKeys(new Set()); setEditing(null); setRenaming(false);
    headerPatch.current = null; controlsPatch.current = null;
    setResetDraft(value => value + 1); setSaveError(null); setDataStatus("Черновик отброшен; показаны сохранённые данные");
  }

  function requestNavigation(action: () => void) {
    if (unknownSave) { setSaveError("Результат записи неизвестен. Сначала проверьте результат сохранения."); return; }
    if (busy) { setSaveError("Завершите открытое действие перед переходом."); return; }
    if (dirty) setPendingNavigation(() => action); else action();
  }
  useEffect(() => { onNavigationReady?.({ request: requestNavigation }); return () => onNavigationReady?.(null); });

  function acceptSaved(result: SaveReportDraftResponse, source: ReportMatrixContract, keys: Set<string>) {
    const cells = new Map(result.cells.map(cell => [coordinateKey(cell.coordinate), cell]));
    const saved = updateCells(source, keys, cell => {
      const value = cells.get(coordinateKey(cell.coordinate));
      const { issue: _issue, ...rest } = cell;
      return { ...rest, ...(value ? { value: value.value, state: value.state } : { state: { ...cell.state, persistence: "saved" as const } }) };
    });
    const next = { ...saved, matrix_revision: result.matrix_revision, presentation: result.presentation, title: result.presentation.title ?? saved.title };
    baselineMatrix.current = structuredClone(next); latestMatrix.current = next; onChange(next);
    setDirtyKeys(new Set()); setEditing(null); setRenaming(false); setResetDraft(value => value + 1);
    headerPatch.current = null; controlsPatch.current = null; setHeaderDirty(false); setControlsDirty(false); setUnknownSave(null); setCanRetryUnknown(false);
    setDataStatus("Данные сохранены");
    setSignatureStatus(result.verification_state === "verified" ? "Отчёт подтверждён" : result.verification_state === "stale" ? "Подтверждение устарело: отчёт изменён" : "Данные сохранены, отчёт не подтверждён");
    setBackupStatus(result.backup_state === "complete" ? "Резервная копия создана" : result.backup_state === "post_backup_warning" ? "Данные сохранены; резервная копия не создана" : "Резервная копия: результат не получен");
    setSaveError(result.verification_error?.message ?? result.backup_warning ?? null);
    setVerificationRefresh(value => value + 1);
  }

  async function saveChanges(): Promise<boolean> {
    if (saving || unknownSave) return false;
    if (headerDirty && !headerPatch.current) { setSaveError("Примените изменение состава кодов отдельно. Затем сохраняйте количественные данные общим пакетом."); return false; }
    let source = latestMatrix.current;
    const keys = new Set(dirtyKeys);
    if (editing) {
      const parsed = parseCellDraft(editing.draft);
      if (!parsed.valid) { setSaveError(parsed.message); return false; }
      source = updateCell(source, editing, cell => ({ ...cell, value: parsed.value, state: { ...cell.state, persistence: "dirty" } }));
      const cell = source.rows[editing.row]?.cells[editing.column]; if (cell) keys.add(cellKey(cell));
      setEditing(null); setDirtyKeys(keys); onChange(source); latestMatrix.current = source;
    }
    const presentation: ReportDraftPresentation = { ...headerPatch.current, ...controlsPatch.current, ...(renaming ? { title: titleDraft.trim() } : {}) };
    for (const field of [presentation.plans, presentation.actuals, ...Object.values(presentation.production_code_plans ?? {}), ...Object.values(presentation.production_code_actuals ?? {})]) {
      for (const value of Object.values(field ?? {})) {
        const parsed = parseCellDraft(value);
        if (!parsed.valid) { setSaveError(parsed.message); return false; }
      }
    }
    if (renaming && !titleDraft.trim()) { setSaveError("Введите название отчёта."); return false; }
    const request: SaveReportDraftRequest = { ...query, base_revision: baselineMatrix.current.matrix_revision, idempotency_key: newIdempotencyKey(),
      confirmation, changes: source.rows.flatMap(row => row.cells.filter(cell => keys.has(cellKey(cell))).map(cell => ({ coordinate: cell.coordinate, value: cell.value }))),
      ...(Object.keys(presentation).length ? { presentation } : {}),
    };
    if (!request.changes.length && !request.presentation) return true;
    setSaving(true); setSaveError(null); setDataStatus("Сохранение черновика…");
    try {
      let result: SaveReportDraftResponse;
      if (gateway.saveReportDraft) result = await gateway.saveReportDraft(request);
      else {
        if (request.presentation) throw Object.assign(new Error("Сборка не поддерживает пакетное сохранение. Обновите программу."), { outcomeKnown: true });
        const saved = await gateway.saveReportCells(request);
        result = { ...saved, data_state: "committed", operation_revision: saved.matrix_revision, current_revision: saved.matrix_revision, idempotent_replay: false, presentation: source.presentation ?? {}, verification_state: saved.verification?.status === "VERIFIED" ? "verified" : saved.verification_error ? "verification_error" : "pending", backup_state: "pending" };
      }
      acceptSaved(result, source, keys);
      try {
        const refreshed = await gateway.getReportMatrix(query);
        baselineMatrix.current = structuredClone(refreshed); latestMatrix.current = refreshed; onChange(refreshed);
      } catch { setSaveError("Данные сохранены. Не удалось обновить расчёты; повторно откройте форму."); }
      return true;
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Сохранение не выполнено";
      if (reason instanceof Error && "outcomeUnknown" in reason && reason.outcomeUnknown === true) {
        setPendingNavigation(null); setCanRetryUnknown(false); setUnknownSave(request); setDataStatus("Результат сохранения неизвестен");
        setSaveError("Ответ не получен. Черновик сохранён в открытой форме. Проверьте результат записи перед повтором или переходом.");
      } else {
        setDataStatus("Черновик не сохранён"); setSaveError(message);
        onChange(updateCells(source, keys, cell => ({ ...cell, state: { ...cell.state, persistence: "error" }, issue: { code: "SAVE_FAILED", message } })));
      }
      return false;
    } finally { setSaving(false); }
  }

  async function reconcileSave() {
    if (!unknownSave || !gateway.getReportDraftSaveResult || saving) return;
    setSaving(true); setSaveError(null);
    try {
      const { report_type, organization_id, year, confirmation: savedConfirmation, idempotency_key } = unknownSave;
      const result = await gateway.getReportDraftSaveResult({ report_type, organization_id, idempotency_key, ...(year === undefined ? {} : { year }), ...(savedConfirmation ? { confirmation: savedConfirmation } : {}) });
      if (result.data_state === "unknown") { setCanRetryUnknown(true); setSaveError("Результат не найден. Можно повторить тот же пакет с тем же идентификатором: программа проверит предыдущую запись и не создаст дубли. Новый пакет и отбрасывание заблокированы."); return; }
      acceptSaved(result, latestMatrix.current, new Set(dirtyKeys));
      try { const refreshed = await gateway.getReportMatrix(query); baselineMatrix.current = structuredClone(refreshed); latestMatrix.current = refreshed; onChange(refreshed); }
      catch { setSaveError("Данные сохранены. Не удалось обновить расчёты; повторно откройте форму."); }
    } catch (reason) { setSaveError(reason instanceof Error ? reason.message : "Результат записи не проверен"); }
    finally { setSaving(false); }
  }

  async function retryUnknownSave() {
    if (!unknownSave || !canRetryUnknown || !gateway.saveReportDraft || saving) return;
    setSaving(true); setSaveError(null);
    try {
      const result = await gateway.saveReportDraft(unknownSave);
      acceptSaved(result, latestMatrix.current, new Set(dirtyKeys));
      try { const refreshed = await gateway.getReportMatrix(query); baselineMatrix.current = structuredClone(refreshed); latestMatrix.current = refreshed; onChange(refreshed); }
      catch { setSaveError("Данные сохранены. Не удалось обновить расчёты; повторно откройте форму."); }
    } catch (reason) { setSaveError(`Результат прежней записи ещё не подтверждён. ${reason instanceof Error ? reason.message : "Повтор не завершён"}`); }
    finally { setSaving(false); }
  }

  async function startImport(): Promise<void> {
    if (navigationBlocked || previewBusy || !matrix.capabilities.import.enabled) return;
    setExcelBusy("import");
    setExcelError(null);
    setExcelMessage(null);
    try {
      const preview = await gateway.validateImport({
        ...query, mode: importMode,
        report_type: matrix.report_type,
        organization_id: matrix.organization_id,
      });
      if (!preview.cancelled) setImportPreview({ ...preview, mode: importMode });
    } catch (reason: unknown) {
      setExcelError(reason instanceof Error ? reason.message : "Excel-файл не проверен");
    } finally {
      setExcelBusy(null);
    }
  }

  async function confirmImport(): Promise<void> {
    if (importPreview?.batch_id === undefined || !importReviewed || unknownSave || dirty || saving || presentationBusy || excelBusy !== null || printing || importPreview.validation_pending || (importPreview.error_count ?? 0) > 0 || importPreview.status === "INVALID" || importPreview.already_imported || (importPreview.reference_workbook && !importPreview.transfer_validated)) return;
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
      baselineMatrix.current = structuredClone(refreshed); latestMatrix.current = refreshed; onChange(refreshed);
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
    if (navigationBlocked || previewBusy || !adminMode) return;
    if (!window.confirm("Убрать производителя из рабочего поля? Сохранённая история останется доступна.")) return;
    setPresentationBusy(true); setSaveError(null);
    try {
      const layout = await gateway.getReportLayout({ report_type: matrix.report_type, organization_id: matrix.organization_id });
      const rows = layout.rows.map(row => row.id === workspaceId && row.configuration?.subsidiary ? { ...row, configuration: { ...row.configuration, subsidiary: { ...row.configuration.subsidiary, suppliers: row.configuration.subsidiary.suppliers.map(s => s.id === supplierId ? { ...s, archived: true } : s) } } } : row);
      await gateway.saveReportLayout({ report_type: matrix.report_type, organization_id: matrix.organization_id, rows });
      const refreshed = await gateway.getReportMatrix(query);
      baselineMatrix.current = structuredClone(refreshed); latestMatrix.current = refreshed; onChange(refreshed);
    } catch (e) { setSaveError(e instanceof Error ? e.message : String(e)); }
    finally { setPresentationBusy(false); }
  }

  async function exportExcel(): Promise<void> {
    if (navigationBlocked || previewBusy || !matrix.capabilities.export.enabled) return;
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

  function renderValueCell(rowIndex: number, columnIndex: number) {
    const row = matrix.rows[rowIndex]!;
    const originalCell = row.cells[columnIndex]!;
    const cell = entryLocked(originalCell, columnIndex) ? { ...originalCell, state: { ...originalCell.state, access: "locked" as const }, lock_reason: matrix.time_columns[columnIndex]?.group_label !== subsidiaryMonth ? "Для ввода выберите этот месяц в контексте отчёта" : "План можно изменить в разделе «План и сведения»" } : originalCell;
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
                          onCancel={() => { setEditing(null); setSaveError(null); }}
                        />
                      ) : (
                        <ReportCellView
                          hint={reportCellHint(cell, matrix, row)}
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
  }

  async function printReport() {
    if (!gateway.exportPdf || dirty || unknownSave || printing || previewBusy) return;
    setPrinting(true); setSaveError(null); setExcelMessage(null);
    try {
      const result = await gateway.exportPdf({ ...query, ...confirmation, expected_revision: matrix.matrix_revision });
      if (!result.cancelled) setExcelMessage(`PDF сохранён: ${result.file_name}`);
      setVerificationRefresh(value => value + 1);
    } catch (reason) { setSaveError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setPrinting(false); setPdfOptionsOpen(false); }
  }

  const searchTerm = search.trim().toLocaleLowerCase("ru");
  const matchingRows = searchTerm ? matrix.rows.flatMap((row, index) => [row.group_label, ...Object.values(row.left_values)].join(" ").toLocaleLowerCase("ru").includes(searchTerm) ? [index] : []) : [];
  const matchingSet = new Set(matchingRows);
  function locateRow(row: number, column?: number) {
    if (column !== undefined) setActive({ row, column });
    const target = document.querySelector<HTMLElement>(`[data-matrix-row="${row}"]`);
    target?.scrollIntoView?.({ block: "center", inline: "nearest" });
    if (column !== undefined) target?.querySelector<HTMLElement>(`[data-row="${row}"][data-column="${column}"]`)?.focus();
  }
  function nextMatch() { if (!matchingRows.length) return; const next = (matchIndex + 1) % matchingRows.length; setMatchIndex(next); locateRow(matchingRows[next]!); }
  function nextError() {
    const errors = matrix.rows.flatMap((row, rowIndex) => row.cells.flatMap((cell, column) => cell.issue && visibleSet.has(column) ? [{ row: rowIndex, column }] : []));
    if (!errors.length) { setSaveError("В показанном месяце ошибок ячеек нет."); return; }
    const next = (errorIndex + 1) % errors.length; setErrorIndex(next); const target = errors[next]!; locateRow(target.row, target.column);
  }

  const dailySummaryColumns = matrix.daily_summary ? [
    { id: "ytd", label: "Накопительный итог", kind: "through_month" as const, index: confirmation.month - 1 },
  ] : [];
  const dailyRows = new Map(matrix.daily_summary?.rows.map(row => [row.row_id, row]));

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
              <button className="button primary" disabled={presentationBusy || !titleDraft.trim()}><UiIcon name="check" />Применить название</button>
              <button className="button secondary" type="button" disabled={presentationBusy} onClick={() => setRenaming(false)}>Отмена</button>
            </form> : <h2 id="matrix-title">{matrix.title}</h2>}
            {reportSettings && !renaming && (gateway.saveReportDraft || gateway.saveReportPresentation) && <button type="button" className="mini-button" aria-label="Переименовать отчёт" disabled={busy || unknownSave !== null} onClick={() => { setTitleDraft(matrix.title); setRenaming(true); }}><UiIcon name="edit" /> Переименовать отчёт</button>}
          </div>
          {previewBusy && <p role="status">Пересчёт…</p>}
        </div>
        <div className="toolbar-actions">
          <button
            type="button"
            className="button primary"
            disabled={(!dirty) || (editing !== null && !parseCellDraft(editing.draft).valid) || unknownSave !== null || saving || printing || excelBusy !== null || presentationBusy || !matrix.capabilities.save.enabled}
            onClick={() => void saveChanges()}
          >
            {saving ? "Сохранение…" : `Сохранить${dirtyKeys.size > 0 ? ` (${dirtyKeys.size})` : ""}`}
            <UiIcon name="save" />
          </button>
          <button type="button" className="button secondary" disabled={!gateway.exportPdf || navigationBlocked || previewBusy} title={headerDirty || controlsDirty || dirtyKeys.size > 0 ? "Сначала сохраните изменения" : undefined} onClick={() => setPdfOptionsOpen(true)}><UiIcon name="print" />{printing ? "Подготовка PDF…" : "Печать / PDF А4"}</button>
          <button type="button" className="button secondary" aria-expanded={moreOpen} aria-controls="report-more-actions" onClick={() => setMoreOpen(open => !open)}>Ещё<UiIcon name={moreOpen ? "chevron-up" : "chevron-down"} /></button>
        </div>
      </div>
      <div id="report-more-actions" className="report-more-actions" hidden={!moreOpen}>
        <div className="toolbar-actions">
          <label>Режим импорта Excel <select aria-label="Режим импорта Excel" value={importMode} disabled={navigationBlocked} onChange={event => setImportMode(event.target.value as ImportMode)}>
            <option value="create">Создать отчёт</option><option value="append">Дополнить отчёт</option><option value="update">Обновить выбранные значения</option>
          </select></label>
          <button
            type="button"
            className="button secondary"
            disabled={
              !matrix.capabilities.import.enabled || navigationBlocked || previewBusy
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
              !matrix.capabilities.export.enabled || navigationBlocked || previewBusy
            }
            title={exportReason}
            onClick={() => void exportExcel()}
          >
            {excelBusy === "export" ? "Выгрузка…" : "Экспорт Excel"}
            <UiIcon name="export" />
          </button>
        </div>

      </div>
      <div className="matrix-search" aria-label="Поиск в отчёте"><label>Найти позицию или производителя<input type="search" value={search} onChange={event => { setSearch(event.target.value); setMatchIndex(-1); }} /></label><span role="status">{searchTerm ? `Найдено строк: ${matchingRows.length}. Все строки отчёта остаются видимыми.` : ""}</span><button type="button" className="button secondary" disabled={!matchingRows.length} onClick={nextMatch}>Следующее совпадение</button><button type="button" className="button secondary" onClick={nextError}>Следующая ошибка</button></div>
      <div className="draft-status" role="status" aria-label="Состояние сохранения"><span>{unknownSave ? dataStatus : dirty ? "Есть несохранённые изменения" : dataStatus}</span><span>{signatureStatus}</span><span>{backupStatus}</span></div>
      {unknownSave && <button type="button" className="button secondary" disabled={saving} onClick={() => void reconcileSave()}>Проверить результат сохранения</button>}
      {unknownSave && canRetryUnknown && <button type="button" className="button secondary" disabled={saving} onClick={() => void retryUnknownSave()}>Повторить тот же пакет</button>}
      <MonthlyReportActions controlledMonth={subsidiaryMonth} weeks={stockWeeks} gateway={gateway} query={query} revision={matrix.matrix_revision} title={matrix.title} refresh={verificationRefresh} blocked={navigationBlocked || previewBusy} />

      {matrix.subsidiary && <ProductionHeader onSaveReady={save => { headerSave.current = save; }} workspaceMode={workspaceMode} presentation={matrix.presentation ?? {}} year={matrix.year!} headSite={!!matrix.head_site} selectedMonth={subsidiaryMonth} resetDraft={resetDraft} onDraftChange={updateHeaderPatch}
        blocked={printing || renaming || unknownSave !== null || saving || excelBusy !== null}
        onSave={saveHeader} onDirtyChange={setHeaderDirty} onBusyChange={setPresentationBusy} />}
      {matrix.subsidiary && <SubsidiaryControls onVerified={() => setVerificationRefresh(value => value + 1)} workspaceMode={workspaceMode} onSaveReady={save => { controlsSave.current = save; }} onDirtyChange={setControlsDirty} onDraftChange={updateControlsPatch} resetDraft={resetDraft} periodBlocked={busy || unknownSave !== null} matrix={matrix} gateway={gateway} onBusy={setPresentationBusy} blocked={printing || renaming || unknownSave !== null || saving || excelBusy !== null} onChange={onChange} month={subsidiaryMonth} {...(startPeriod ? { startPeriod } : {})}
        onMonth={month => requestNavigation(() => { setSubsidiaryMonth(month); setExpandedMonths(new Set([month])); })} week={stockWeeks[subsidiaryMonth] ?? ""} onWeek={week => requestNavigation(() => setStockWeeks(current => ({ ...current, [subsidiaryMonth]: week })))} />}
      {!!matrix.legacy_cells?.length && <details className="legacy-facts"><summary>Данные прежней формы — {matrix.legacy_cells.length} ячеек (не включены в расход)</summary><p>Значения сохранены в исходном смысле. Для переноса в новую структуру используйте проверку импорта.</p><table><thead><tr><th>Позиция / ID</th><th>Показатель</th><th>Период</th><th>Значение</th></tr></thead><tbody>{matrix.legacy_cells.map((cell, i) => <tr key={i}><td>{cell.coordinate.component_id ?? cell.coordinate.product_id}</td><td>{cell.coordinate.metric_code}</td><td>{cell.coordinate.period_start}</td><td>{inputValue(cell.value)}</td></tr>)}</tbody></table></details>}
      {matrix.calendar_notice && <p role="note">{matrix.calendar_notice}</p>}

      {!matrix.subsidiary && <label className="entry-month">Месяц отчёта<select value={subsidiaryMonth} disabled={editing !== null || printing} onChange={event => { const month = event.target.value; requestNavigation(() => { setSubsidiaryMonth(month); setExpandedMonths(new Set([month])); }); }}>{monthLabels.map(month => <option key={month} value={month}>{monthName(month)} {matrix.year}</option>)}</select></label>}
      {matrix.subsidiary && <details className="report-month-options" open={adminMode || undefined}><summary>Показать несколько месяцев</summary>
      <nav className="month-controls" aria-label="Месяцы отчёта">
        <button type="button" disabled={editing !== null} onClick={() => {
          const next = { ...widths, ...Object.fromEntries(matrix.time_columns.map(column => [column.id, matrix.subsidiary && !["SUPPLIED", "USED"].includes(column.kind ?? "") ? 110 : 64])) };
          setWidths(next); persistWidths(next);
        }}>Компактные столбцы: 5 цифр</button>
        {matrix.year && <strong>{matrix.year}</strong>}
        {monthLabels.map((month) => <button key={month} type="button" aria-expanded={expandedMonths.has(month)} disabled={editing !== null}
          onClick={() => toggleMonth(month)}>{expandedMonths.has(month) ? "▾" : "▸"} {monthName(month)}</button>)}
        <button type="button" disabled={editing !== null} onClick={() => setExpandedMonths(new Set(monthLabels))}><UiIcon name="chevron-down" />Раскрыть все</button>
        <button type="button" disabled={editing !== null} onClick={() => setExpandedMonths(new Set())}><UiIcon name="chevron-up" />Свернуть все</button>
      </nav></details>}

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

      {matrix.subsidiary ? <SourceMatrixTable matrix={{ ...matrix, time_columns: timeColumns }} summaryMonth={subsidiaryMonth} stockWeek={stockWeeks[subsidiaryMonth] ?? matrix.time_columns.filter(column => column.group_label === subsidiaryMonth && column.kind === "USED").at(-1)?.id ?? ""}
        matchingRows={matchingSet} visibleIndices={visibleIndices} renderValueCell={renderValueCell} onPaste={previewPaste} leftWidths={widths} renderResizeHandle={resizeHandle}
        blocked={busy || unknownSave !== null} removeBlocked={navigationBlocked || previewBusy} {...(adminMode ? { onRemoveSupplier: (workspaceId: string, supplierId: string) => void removeSupplier(workspaceId, supplierId) } : {})}
        onSelectStockWeek={week => { const month = week.slice(0, 7); requestNavigation(() => { setSubsidiaryMonth(month); setStockWeeks(current => ({ ...current, [month]: week })); }); }} /> : (
      <div className="matrix-scroll" data-testid="matrix-scroll">
        <table
          onPaste={previewPaste}
          className={matrix.subsidiary ? "report-matrix subsidiary-matrix" : "report-matrix"}
          style={{ width: leftColumns.reduce((sum, column) => sum + column.width, 0) + visibleMatrix.time_columns.reduce((sum, column) => sum + column.width, 0) + dailySummaryColumns.length * 140, minWidth: 0 }}
          aria-label={matrix.title}
          aria-rowcount={matrix.rows.length + 2}
          aria-colcount={matrix.left_columns.length + visibleMatrix.time_columns.length + dailySummaryColumns.length}
        >
          <colgroup>
            {leftColumns.map((column) => (
              <col key={column.id} style={{ width: column.width }} />
            ))}
            {dailySummaryColumns.map(column => <col key={column.id} style={{ width: 140 }} />)}
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
              {dailySummaryColumns.map(column => <th key={column.id} rowSpan={2} scope="col"><HintValue hint={REPORT_FIELD_HINTS.cumulative}>{column.label}</HintValue></th>)}
              {groups.map((group, index) => (
                <th key={`${group.label}-${index}`} colSpan={group.span} scope="colgroup">
                  {matrix.subsidiary ? <button className="month-heading" type="button" disabled={editing !== null} onClick={() => toggleMonth(group.label)}>▾ {monthName(group.label)}</button> : monthName(group.label)}
                </th>
              ))}
            </tr>
            <tr className="matrix-header-leaf-row">
              {visibleMatrix.time_columns.map((column) => (
                <th key={column.id} scope="col">{matrix.subsidiary && !matrix.head_site && column.kind === "USED" ? <button type="button" className="week-heading" title="Показать остаток на конец этой недели" disabled={busy || unknownSave !== null} aria-pressed={(stockWeeks[column.group_label] ?? matrix.time_columns.filter(c => c.group_label === column.group_label && c.kind === "USED").at(-1)?.id) === column.id} onClick={() => requestNavigation(() => { setSubsidiaryMonth(column.group_label); setStockWeeks(current => ({ ...current, [column.group_label]: column.id })); })}>Расход<br />{column.label}</button> : column.label}{resizeHandle(column.id, `${column.group_label} ${column.label}`, column.width)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.rows.map((row, rowIndex) => (
              <tr key={row.id} data-matrix-row={rowIndex} className={matchingSet.has(rowIndex) ? "matrix-search-match" : undefined}>
                {spans.has(rowIndex) && matrix.left_columns[0] !== undefined && (
                  <th
                    rowSpan={spans.get(rowIndex)}
                    className="sticky-left matrix-group-cell"
                    style={{ left: offsets[0], width: leftColumns[0]?.width }}
                    scope="rowgroup"
                  >
                    <HintValue hint={identityHint(matrix.left_columns[0].id)}>{row.left_values[matrix.left_columns[0].id]}</HintValue>
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
                    <HintValue hint={identityHint(column.id)}>{row.left_values[column.id]}</HintValue>
                    {adminMode && matrix.subsidiary && column.id === "party" && !row.archived && row.workspace_id && row.supplier_id && <button type="button" className="mini-button supplier-remove" title="Убрать производителя" aria-label="Убрать производителя" disabled={navigationBlocked || previewBusy} onClick={() => void removeSupplier(row.workspace_id!, row.supplier_id!)}><UiIcon name="trash" /></button>}
                    {(matrix.subsidiary ? column.id === "position" : leftIndex === 0) && row.category && row.category !== "UNSPECIFIED" && (
                      <small className="position-category">{CATEGORY_LABELS[row.category] ?? row.category}</small>
                    )}
                    {(matrix.subsidiary ? column.id === "position" : leftIndex === 0) && row.image && (
                      <HintValue hint={identityHint("photo")}><img className="matrix-position-image" src={row.image} alt={`Изображение: ${row.group_label}`} /></HintValue>
                    )}
                    {leftIndex === matrix.left_columns.length - 2 &&
                      row.indicator_detail != null && !(matrix.daily_summary && row.indicator_detail.kind === "SUM") && (
                        <span className="indicator-detail">
                          <span>{row.indicator_detail.label}</span>
                          {row.indicator_detail.kind === "SUM" && (
                            <HintValue hint="Сумма указанных значений этой строки. Заполните дневные ячейки строки. Пустые ячейки не являются подтверждённым нулём."><strong>
                              {sumCellValues(row.cells.map((cell) => cell.value)) ?? "—"}
                            </strong></HintValue>
                          )}
                        </span>
                      )}
                  </th>
                ))}
                {dailySummaryColumns.map(column => <td className="daily-summary-value" key={column.id}><HintValue hint={cumulativeRowHint(row, matrix)}>{dailyRows.get(row.id)?.[column.kind]?.[column.index] ?? ""}</HintValue></td>)}
                {row.cells.map((_, columnIndex) => visibleSet.has(columnIndex) ? renderValueCell(rowIndex, columnIndex) : null)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>)}
      {!matrix.subsidiary && <DailyMonthlySummary summary={matrix.daily_summary} />}

      {pastePreview !== null && (
        <div className="excel-dialog-backdrop" role="presentation">
          <section className="excel-dialog" role="dialog" aria-modal="true" aria-labelledby="paste-preview-title"
            onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); setPastePreview(null); } }}>
            <h3 id="paste-preview-title">Проверка вставки из Excel</h3>
            <p>Проверено ячеек: {pastePreview.length}. Пустые поля останутся пустыми, нули — подтверждёнными нулями. Десятичная запятая приведена к точке.</p>
            <ol className="excel-issues">{pastePreview.slice(0, 30).map((item, index) => <li key={index}>{item.label}: <strong>{item.value.kind === "QUANTITY" ? item.value.quantity : "данные не представлены"}</strong></li>)}</ol>
            {pastePreview.length > 30 && <p>Показаны первые 30 ячеек из {pastePreview.length}.</p>}
            <p>После вставки нажмите «Сохранить», чтобы записать значения.</p>
            <div className="excel-dialog-actions">
              <button type="button" className="button secondary" autoFocus onClick={() => setPastePreview(null)}>Отмена вставки</button>
              <button type="button" className="button primary" onClick={applyPaste}><UiIcon name="paste" />Вставить проверенный диапазон</button>
            </div>
          </section>
        </div>
      )}

      {importPreview !== null && (
        <div className="excel-dialog-backdrop" role="presentation">
          <section
            className={importPreview.reference_workbook || importPreview.metadata || importPreview.value_changes ? "excel-dialog excel-transfer-dialog" : "excel-dialog"}
            role="dialog"
            aria-modal="true"
            aria-labelledby="excel-preview-title"
          >
            <header className="excel-preview-header"><h3 id="excel-preview-title">Проверка импорта Excel</h3><p>Файл → Распознавание → Проверка → Импорт</p></header>
            <p className="excel-file-name">{importPreview.file_name}</p>
            {importPreview.reference_workbook && <ReferenceTransfer
              book={importPreview.reference_workbook} matrix={matrix} gateway={gateway} preview={importPreview}
              onReady={setImportPreview} />}
            {!importPreview.reference_workbook && (importPreview.metadata?.sheets || importPreview.metadata?.review_sheets) && <CanonicalSheetReview preview={importPreview} gateway={gateway} query={query} onChange={setImportPreview} />}
            <ImportChangePreview preview={importPreview} showValues={!importPreview.reference_workbook} />
            <div className="excel-preview-counts">
              <span><strong>{importPreview.validation_pending ? "—" : importPreview.position_count ?? 0}</strong> позиций</span>
              <span><strong>{importPreview.validation_pending ? "—" : importPreview.dictionary_count ?? 0}</strong> новых справочников</span>
              <span><strong>{importPreview.validation_pending ? "—" : importPreview.new_count ?? 0}</strong> новых значений</span>
              <span><strong>{importPreview.validation_pending ? "—" : importPreview.changed_count ?? 0}</strong> изменённых</span>
              <span><strong>{importPreview.validation_pending ? "—" : importPreview.same_count ?? 0}</strong> без изменений</span>
              <span><strong>{importPreview.validation_pending ? "—" : importPreview.skipped_count ?? 0}</strong> пропусков</span>
              <span className={(importPreview.error_count ?? 0) > 0 ? "has-errors" : ""}>
                <strong>{importPreview.validation_pending ? "—" : importPreview.error_count ?? 0}</strong> ошибок
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
            <label className="import-full-review"><input type="checkbox" checked={importReviewed} disabled={excelBusy === "commit" || !!importPreview.validation_pending || (importPreview.error_count ?? 0) > 0} onChange={event => setImportReviewed(event.target.checked)} />Я просмотрел весь пакет: значения «было → стало», структуру, периоды и пропуски.</label>
            <div className="excel-dialog-actions">
              <button
                className="button secondary"
                type="button"
                disabled={excelBusy === "commit"}
                onClick={() => setImportPreview(null)}
              >
                <UiIcon name="close" />Закрыть
              </button>
              <button
                className="button primary"
                type="button"
                disabled={
                  !importReviewed || (!!importPreview.reference_workbook && !importPreview.transfer_validated) || !!importPreview.validation_pending || !importPreview.batch_id || importPreview.status === "INVALID" ||
                  excelBusy === "commit" ||
                  (importPreview.error_count ?? 0) > 0 ||
                  importPreview.already_imported
                }
                onClick={() => void confirmImport()}
              >
                <UiIcon name="import" />{excelBusy === "commit" ? "Импорт…" : "Подтвердить импорт"}
              </button>
            </div>
          </section>
        </div>
      )}
      {pdfOptionsOpen && <SavedReportPdfDialog title={matrix.title} period={`${monthName(subsidiaryMonth)} ${matrix.year ?? ""}`} {...(stockWeeks[subsidiaryMonth] ? { week: stockWeeks[subsidiaryMonth] } : {})} busy={printing} onConfirm={() => void printReport()} onClose={() => setPdfOptionsOpen(false)} />}

      {pendingNavigation && <DraftTransitionDialog count={dirtyKeys.size + Number(headerDirty) + Number(controlsDirty) + Number(renaming) + Number(editing !== null)} busy={saving || unknownSave !== null} error={saveError}
        onStay={() => setPendingNavigation(null)} onDiscard={() => { const action = pendingNavigation; discardDraft(); setPendingNavigation(null); action(); }}
        onSave={() => void saveChanges().then(saved => { if (saved) { const action = pendingNavigation; setPendingNavigation(null); action(); } })} />}
    </section>
  );
}
