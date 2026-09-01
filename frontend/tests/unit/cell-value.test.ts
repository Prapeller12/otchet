import { describe, expect, it } from "vitest";

import {
  displayValue,
  isConfirmedZero,
  parseCellDraft,
} from "../../src/widgets/report-matrix/cell-value";

describe("matrix cell values", () => {
  it("keeps an empty cell distinct from a confirmed zero", () => {
    const empty = parseCellDraft("   ");
    const zero = parseCellDraft("0");

    expect(empty).toEqual({ valid: true, value: { kind: "DATA_NOT_PROVIDED" } });
    expect(zero).toEqual({
      valid: true,
      value: { kind: "QUANTITY", quantity: "0" },
    });
    if (empty.valid && zero.valid) {
      expect(displayValue(empty.value)).toBe("—");
      expect(displayValue(zero.value)).toBe("0");
      expect(isConfirmedZero(empty.value)).toBe(false);
      expect(isConfirmedZero(zero.value)).toBe(true);
    }
  });

  it.each(["01", "+1", ".5", "5.", "1,5", "1e3"])(
    "rejects non-canonical decimal input %s",
    (draft) => {
      expect(parseCellDraft(draft)).toMatchObject({ valid: false });
    },
  );
});
