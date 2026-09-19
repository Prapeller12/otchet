import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { DailyMonthlySummary } from "../../src/widgets/report-matrix/DailyMonthlySummary";
import { addDailyCodeFields } from "../../src/features/workspace-settings/daily-code-fields";

afterEach(cleanup);

it("renders all twelve months and readonly component triples preserving empty and zero", () => {
  render(<DailyMonthlySummary summary={{ year: 2026, periods: [], rows: [], components: [{
    group_id: "part", party: "Поставщик", position: "Составная часть", rows: ["RECEIVED", "USED", "BALANCE"].map((metric_code, index) => ({
      metric_code, row_id: metric_code, annual: "0", monthly: [index === 0 ? "0" : "", ...Array<string>(11).fill("")], through_month: Array<string>(12).fill("0"),
    })),
  }] }} />);
  expect(screen.getByText("Декабрь")).toBeVisible();
  const january = screen.getByText("Январь").closest("tr")!;
  expect(within(january).getAllByRole("cell").map(cell => cell.textContent)).toEqual(["0", "", ""]);
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

it("appends separate code rows in plan/released/arrival order without replacing existing facts", () => {
  const existing = [{ code: "WRK_DAILY_ASSEMBLY_PLAN", label: "План выпуска", formula: "" }];
  const updated = addDailyCodeFields(existing, "А, Б", "ASSEMBLY");
  expect(updated[0]).toBe(existing[0]);
  expect(updated.slice(1).map(row => row.label)).toEqual(["План выпуска (А)", "План выпуска (Б)", "Выпущено (А)", "Выпущено (Б)", "Прибытие (А)", "Прибытие (Б)"]);
  expect(new Set(updated.map(row => row.code)).size).toBe(7);
  expect(() => addDailyCodeFields(updated, "А", "ASSEMBLY")).toThrow(/уже добавлены/);
  expect(() => addDailyCodeFields(existing, "А, А", "ASSEMBLY")).toThrow(/повторяться/);
  expect(existing).toHaveLength(1);
});
