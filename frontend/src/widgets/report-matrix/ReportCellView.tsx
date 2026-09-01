import { useEffect, useRef, type KeyboardEvent } from "react";

import type { MatrixCellContract } from "../../shared/api/application-gateway";
import { displayValue, isConfirmedZero } from "./cell-value";
import type { MatrixPosition } from "./matrix-navigation";

type ReportCellViewProps = {
  cell: MatrixCellContract;
  position: MatrixPosition;
  active: boolean;
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
  onActivate,
  onEdit,
  onKeyDown,
}: ReportCellViewProps) {
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (active) buttonRef.current?.focus({ preventScroll: true });
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
    <button
      ref={buttonRef}
      type="button"
      className={classNames}
      tabIndex={active ? 0 : -1}
      aria-readonly={cell.state.access !== "editable"}
      aria-invalid={cell.state.persistence === "error"}
      aria-label={`${valueText(cell)}, ${accessText(cell)}${issueText}`}
      title={cell.issue?.message ?? cell.lock_reason}
      onClick={() => onActivate(position)}
      onDoubleClick={() => {
        if (cell.state.access === "editable") onEdit(position);
      }}
      onKeyDown={(event) => onKeyDown(event, position)}
    >
      <span className="cell-value">{displayValue(cell.value)}</span>
      <span className="cell-markers" aria-hidden="true">
        {cell.state.access === "calculated" && (
          <span className="cell-marker marker-calculated">Σ</span>
        )}
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
    </button>
  );
}
