import {
  useEffect,
  useMemo,
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
import { CellEditor } from "./CellEditor";
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

  const spans = useMemo(() => groupSpans(matrix), [matrix]);
  const groups = useMemo(() => headerGroups(matrix), [matrix]);
  const offsets = useMemo(() => stickyOffsets(matrix), [matrix]);

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
    onStatusChange(`${identifiers.join(" · ")} · ${column.label}`);
  }, [active, matrix, onStatusChange]);

  function beginEdit(position: MatrixPosition): void {
    const cell = matrix.rows[position.row]?.cells[position.column];
    if (cell?.state.access !== "editable") return;
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
    setEditing(null);
    setSaveError(null);

    if (move === "enter") setActive(moveAfterEnter(nextMatrix, position));
    if (move === "tab") setActive(moveByTab(nextMatrix, position, backwards));
  }

  function handleCellKeyDown(
    event: KeyboardEvent<HTMLButtonElement>,
    position: MatrixPosition,
  ): void {
    if (event.key.startsWith("Arrow")) {
      event.preventDefault();
      setActive(
        moveByArrow(
          position,
          event.key as ArrowKey,
          matrix.rows.length,
          matrix.time_columns.length,
        ),
      );
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      setActive(moveByTab(matrix, position, event.shiftKey));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      beginEdit(position);
    }
  }

  async function saveChanges(): Promise<void> {
    if (dirtyKeys.size === 0 || saving) return;
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
      const result = await gateway.commitImport({ batch_id: importPreview.batch_id });
      const refreshed = await gateway.getReportMatrix({
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

  async function exportExcel(): Promise<void> {
    setExcelBusy("export");
    setExcelError(null);
    setExcelMessage(null);
    try {
      const result = await gateway.exportReport({
        report_type: matrix.report_type,
        organization_id: matrix.organization_id,
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
            <h2 id="matrix-title">{matrix.title}</h2>
            <span className="reference-badge">Рабочая форма</span>
          </div>
          <p>{matrix.subtitle}</p>
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
          </button>
          <button
            type="button"
            className="button primary"
            disabled={dirtyKeys.size === 0 || saving || !matrix.capabilities.save.enabled}
            onClick={() => void saveChanges()}
          >
            {saving ? "Сохранение…" : `Сохранить${dirtyKeys.size > 0 ? ` (${dirtyKeys.size})` : ""}`}
          </button>
        </div>
      </div>

      <div className="source-notice" role="note">
        <strong>{matrix.source_notice}</strong>
        <span>
          {importReason ??
            "Excel: выгрузите книгу, заполните жёлтые ячейки и импортируйте её обратно."}
        </span>
      </div>

      {saveError !== null && (
        <div className="save-error" role="alert">
          Изменения сохранены локально в форме, но backend их не принял: {saveError}
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
          className="report-matrix"
          aria-label={matrix.title}
          aria-rowcount={matrix.rows.length + 2}
          aria-colcount={matrix.left_columns.length + matrix.time_columns.length}
        >
          <colgroup>
            {matrix.left_columns.map((column) => (
              <col key={column.id} style={{ width: column.width }} />
            ))}
            {matrix.time_columns.map((column) => (
              <col key={column.id} style={{ width: column.width }} />
            ))}
          </colgroup>
          <thead>
            <tr className="matrix-header-group-row">
              {matrix.left_columns.map((column, index) => (
                <th
                  key={column.id}
                  rowSpan={2}
                  className="sticky-left sticky-header"
                  style={{ left: offsets[index] }}
                  scope="col"
                >
                  {column.label}
                </th>
              ))}
              {groups.map((group, index) => (
                <th key={`${group.label}-${index}`} colSpan={group.span} scope="colgroup">
                  {group.label}
                </th>
              ))}
            </tr>
            <tr className="matrix-header-leaf-row">
              {matrix.time_columns.map((column) => (
                <th key={column.id} scope="col">{column.label}</th>
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
                    style={{ left: offsets[0], minWidth: matrix.left_columns[0].width }}
                    scope="rowgroup"
                  >
                    {row.left_values[matrix.left_columns[0].id]}
                  </th>
                )}
                {matrix.left_columns.slice(1).map((column, leftIndex) => (
                  <th
                    key={column.id}
                    className="sticky-left matrix-indicator-cell"
                    style={{
                      left: offsets[leftIndex + 1],
                      minWidth: column.width,
                    }}
                    scope="row"
                  >
                    <span>{row.left_values[column.id]}</span>
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
                  const position = { row: rowIndex, column: columnIndex };
                  const isEditing =
                    editing?.row === rowIndex && editing.column === columnIndex;
                  return (
                    <td
                      key={cell.column_id}
                      className={isEditing ? "matrix-value-cell is-editing" : "matrix-value-cell"}
                      style={{ minWidth: matrix.time_columns[columnIndex]?.width }}
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
                          cell={cell}
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
            className="excel-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="excel-preview-title"
          >
            <h3 id="excel-preview-title">Проверка импорта Excel</h3>
            <p className="excel-file-name">{importPreview.file_name}</p>
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
