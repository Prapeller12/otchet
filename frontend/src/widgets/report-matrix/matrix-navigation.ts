import type { ReportMatrixContract } from "../../shared/api/application-gateway";

export type MatrixPosition = { row: number; column: number };

export type ArrowKey = "ArrowUp" | "ArrowDown" | "ArrowLeft" | "ArrowRight";

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}
export function moveByArrow(
  position: MatrixPosition,
  key: ArrowKey,
  rowCount: number,
  columnCount: number,
): MatrixPosition {
  const delta = {
    ArrowUp: { row: -1, column: 0 },
    ArrowDown: { row: 1, column: 0 },
    ArrowLeft: { row: 0, column: -1 },
    ArrowRight: { row: 0, column: 1 },
  }[key];

  return {
    row: clamp(position.row + delta.row, 0, Math.max(0, rowCount - 1)),
    column: clamp(
      position.column + delta.column,
      0,
      Math.max(0, columnCount - 1),
    ),
  };
}

function isEditable(matrix: ReportMatrixContract, position: MatrixPosition): boolean {
  return matrix.rows[position.row]?.cells[position.column]?.state.access === "editable";
}

export function moveByTab(
  matrix: ReportMatrixContract,
  position: MatrixPosition,
  backwards: boolean,
): MatrixPosition {
  const columnCount = matrix.time_columns.length;
  const total = matrix.rows.length * columnCount;
  const start = position.row * columnCount + position.column;
  const direction = backwards ? -1 : 1;

  for (
    let candidate = start + direction;
    candidate >= 0 && candidate < total;
    candidate += direction
  ) {
    const next = {
      row: Math.floor(candidate / columnCount),
      column: candidate % columnCount,
    };
    if (isEditable(matrix, next)) return next;
  }
  return position;
}

export function moveAfterEnter(
  matrix: ReportMatrixContract,
  position: MatrixPosition,
): MatrixPosition {
  if (matrix.navigation.enter_direction === "stay") return position;
  const key = matrix.navigation.enter_direction === "down" ? "ArrowDown" : "ArrowRight";
  return moveByArrow(
    position,
    key,
    matrix.rows.length,
    matrix.time_columns.length,
  );
}
