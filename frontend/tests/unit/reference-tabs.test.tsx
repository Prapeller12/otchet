import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../src/app/App";
import { ApplicationGatewayProvider } from "../../src/app/providers/ApplicationGatewayProvider";
import { DemoGateway } from "../../src/shared/api/demo-gateway";

vi.mock("../../src/pages/report-matrix/ReportMatrixPage", () => ({ ReportMatrixPage: () => <div>Матрица</div> }));
vi.mock("../../src/features/reference-reports/ReferenceReport", () => ({ ReferenceReport: ({ identity }: { identity: string }) => <div>Книга: {identity}</div> }));
afterEach(cleanup);

it("separates saved workbooks by tab and opens imports in the matching tab", async () => {
  const referenceReport = vi.fn().mockResolvedValue([
    { id: "head", file_name: "Головной.xlsx", report_type: "HEAD_SITE" },
    { id: "sub", file_name: "Дочерний.xlsx", report_type: "SUBSIDIARY" },
  ]);
  const gateway = Object.assign(new DemoGateway(), { referenceReport });
  render(<ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider>);
  const user = userEvent.setup();
  await screen.findByText("Матрица");
  expect(screen.queryByLabelText("Сохранённые отчёты Excel")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Головная площадка" }));
  const head = await screen.findByLabelText("Сохранённые отчёты Excel");
  expect(within(head).getAllByRole("option").map(o => o.textContent)).toEqual(["Рабочая форма", "Головной.xlsx"]);
  await user.selectOptions(head, "head");
  expect(screen.getByText("Книга: head")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Дочерние общества" }));
  const sub = screen.getByLabelText("Сохранённые отчёты Excel");
  expect(sub).toHaveValue("");
  expect(within(sub).getAllByRole("option").map(o => o.textContent)).toEqual(["Рабочая форма", "Дочерний.xlsx"]);
  expect(screen.queryByText("Книга: head")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Ежедневный отчёт" }));
  expect(screen.queryByLabelText("Сохранённые отчёты Excel")).toBeNull();
  fireEvent(window, new CustomEvent("reference-report-imported", { detail: { id: "sub" } }));
  expect(await screen.findByText("Книга: sub")).toBeVisible();
  expect(screen.getByRole("button", { name: "Дочерние общества" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByLabelText("Сохранённые отчёты Excel")).toHaveValue("sub");
  expect(screen.queryByRole("option", { name: "Головной.xlsx" })).toBeNull();
});
