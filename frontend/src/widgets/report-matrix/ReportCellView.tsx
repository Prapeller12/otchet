import { useEffect, useRef, type KeyboardEvent } from "react";

import type { MatrixCellContract } from "../../shared/api/application-gateway";
import { useFieldHint } from "../../shared/ui/FieldHint";
import { reportCellHint } from "../../shared/config/report-field-hints";
import { displayValue, isConfirmedZero } from "./cell-value";
import type { MatrixPosition } from "./matrix-navigation";

type ReportCellViewProps = {
  cell: MatrixCellContract;
  position: MatrixPosition;
  active: boolean;
  hint?: string | undefined;
  onActivate(position: MatrixPosition): void;
  onEdit(position: MatrixPosition): void;
  onKeyDown(event: KeyboardEvent<HTMLButtonElement>, position: MatrixPosition): void;
};

function accessText(cell: MatrixCellContract): string {
  if (cell.state.access === "calculated") return "расчётная ячейка";
  if (cell.state.access === "locked") return "заблокированная ячейка";
  return "доступна для ввода";
}
function valueText(cell: MatrixCellContract): string {
  if (cell.value.kind === "DATA_NOT_PROVIDED") return "данные не представлены";
  if (isConfirmedZero(cell.value)) return "подтверждённый ноль";
  return `значение ${cell.value.quantity}`;
}

export function ReportCellView({
  cell,
  position,
  active,
  hint,
  onActivate,
  onEdit,
  onKeyDown,
}: ReportCellViewProps) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const help = useFieldHint(hint ?? reportCellHint(cell));

  useEffect(() => {
    if (!active || !buttonRef.current) return;
    const button = buttonRef.current;
    button.focus({ preventScroll: true });
    const viewport = button.closest<HTMLElement>(".matrix-scroll");
    if (!viewport) return;
    const table = button.closest("table");
    const reveal = () => {
      const bounds = viewport.getBoundingClientRect();
      const cell = button.getBoundingClientRect();
      const headers = Array.from(table?.querySelectorAll("thead th") ?? []);
      const frozenRight = Math.max(bounds.left, ...headers.filter(header => {
        const style = getComputedStyle(header);
        return style.position === "sticky" && style.left !== "auto";
      }).map(header => header.getBoundingClientRect().right));
      const headerBottom = Math.max(bounds.top, ...headers.filter(header => getComputedStyle(header).position === "sticky").map(header => header.getBoundingClientRect().bottom));
      if (cell.right > bounds.right) viewport.scrollLeft += cell.right - bounds.right;
      else if (cell.left < frozenRight) viewport.scrollLeft -= frozenRight - cell.left;
      if (cell.bottom > bounds.bottom) viewport.scrollTop += cell.bottom - bounds.bottom;
      else if (cell.top < headerBottom) viewport.scrollTop -= headerBottom - cell.top;
    };
    reveal();
    // Opening context panels, changing a saved width or resizing the window can
    // move the same active cell. Keep it visible without stealing toolbar focus.
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", reveal);
      return () => window.removeEventListener("resize", reveal);
    }
    let resizeFrame: number | undefined;
    const observer = new ResizeObserver(() => {
      if (resizeFrame !== undefined) cancelAnimationFrame(resizeFrame);
      // SourceMatrixTable also updates its frozen prefix after a resize.
      resizeFrame = requestAnimationFrame(() => { resizeFrame = undefined; reveal(); });
    });
    observer.observe(viewport);
    if (table) observer.observe(table);
    return () => { observer.disconnect(); if (resizeFrame !== undefined) cancelAnimationFrame(resizeFrame); };
  }, [active]);

  const classNames = [
    "matrix-cell-button",
    `is-${cell.state.access}`,
    `is-${cell.state.persistence}`,
    cell.value.kind === "DATA_NOT_PROVIDED" ? "is-empty" : "",
    isConfirmedZero(cell.value) ? "is-zero" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const issueText = cell.issue === undefined ? "" : `, ошибка: ${cell.issue.message}`;

  return (
    <><button
      ref={buttonRef}
      type="button"
      data-row={position.row}
      data-column={position.column}
      className={classNames}
      tabIndex={active ? 0 : -1}
      aria-readonly={cell.state.access !== "editable"}
      aria-invalid={cell.state.persistence === "error" || Boolean(cell.issue)}
      aria-label={`${valueText(cell)}, ${accessText(cell)}${issueText}`}
      {...help.hintProps}
      onClick={() => onActivate(position)}
      onDoubleClick={() => {
        if (cell.state.access === "editable") onEdit(position);
      }}
      onKeyDown={(event) => { help.hintProps.onKeyDown(event); onKeyDown(event, position); }}
    >
      <span className="cell-value">{cell.issue?.code === "MISSING_INPUT" ? "Заполните" : displayValue(cell.value)}</span>
      <span className="cell-markers" aria-hidden="true">
        {cell.state.access === "locked" && (
          <span className="cell-marker marker-locked">Б</span>
        )}
        {cell.state.persistence === "error" && (
          <span className="cell-marker marker-error">!</span>
        )}
        {cell.state.persistence === "dirty" && (
          <span className="cell-marker marker-dirty">●</span>
        )}
        {cell.state.persistence === "saving" && (
          <span className="cell-marker marker-saving">…</span>
        )}
      </span>
    </button>{help.hint}</>
  );
}
