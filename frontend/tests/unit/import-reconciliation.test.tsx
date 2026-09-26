import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { ImportReconciliation } from "../../src/features/reference-reports/ImportReconciliation";
it("distinguishes matched, mismatched and unverified totals with reasons and both values", async () => {
  render(<ImportReconciliation rows={[
    { source_cell: "Лист!A1", sheet: "Лист", address: "A1", source_value: "115", program_value: "115", unit: "шт", period: "сентябрь 2026", status: "MATCH", reason: "Совпали точные значения", rounding: "Без округления" },
    { source_cell: "Лист!A2", sheet: "Лист", address: "A2", source_value: "120", program_value: "115", unit: "шт", period: null, status: "MISMATCH", reason: "Исходный остаток отличается", rounding: "Без округления" },
    { source_cell: "Лист!A3", sheet: "Лист", address: "A3", source_value: "50", program_value: null, unit: "%", period: null, status: "UNVERIFIED", reason: "Нет соответствующего расчёта", rounding: "Не применяется" },
  ]} />);
  await userEvent.setup().click(screen.getByText("Сверка контрольных итогов (3)"));
  expect(screen.getByText("Совпадает")).toBeVisible(); expect(screen.getByText("Расхождение")).toBeVisible(); expect(screen.getByText("Не сверено")).toBeVisible();
  expect(screen.getByText("120")).toBeVisible(); expect(screen.getAllByText("115")).toHaveLength(3);
  expect(screen.getByText("Не рассчитано")).toBeVisible(); expect(screen.getByText(/Нет соответствующего расчёта/)).toBeVisible();
});
