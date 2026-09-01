import { describe, expect, it } from "vitest";

import { createDemoMatrix } from "../../src/shared/api/demo-gateway";
import {
  moveAfterEnter,
  moveByArrow,
  moveByTab,
} from "../../src/widgets/report-matrix/matrix-navigation";

describe("matrix keyboard navigation", () => {
  it("moves by arrows without wrapping outside the matrix", () => {
    expect(moveByArrow({ row: 0, column: 0 }, "ArrowUp", 3, 4)).toEqual({
      row: 0,
      column: 0,
    });
    expect(moveByArrow({ row: 1, column: 1 }, "ArrowRight", 3, 4)).toEqual({
      row: 1,
      column: 2,
    });
  });

  it("uses Tab only for editable cells and Enter follows the descriptor", () => {
    const matrix = createDemoMatrix("DAILY_MOVEMENT");
    const fromLastEditableInFirstRow = { row: 0, column: 4 };

    expect(moveByTab(matrix, fromLastEditableInFirstRow, false)).toEqual({
      row: 1,
      column: 0,
    });
    expect(moveAfterEnter(matrix, { row: 0, column: 2 })).toEqual({
      row: 1,
      column: 2,
    });
  });
});
