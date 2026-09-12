import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceSettingsDialog } from "../../src/features/workspace-settings/WorkspaceSettingsDialog";
import { DemoGateway } from "../../src/shared/api/demo-gateway";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each(["DAILY_MOVEMENT"] as const)("copies and inserts formulas without closing %s settings", async report_type => {
  const user = userEvent.setup();
  const clipboard = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
  const gateway = new DemoGateway();
  const query = { report_type, organization_id: "demo-organization" };
  const layout = await gateway.getReportLayout(query);
  layout.rows[0]!.configuration = { category: "PKI", image: "", norm: "3", opening: "0", indicators: [
    { code: "INPUT", label: "Приход", formula: "" },
    { code: "TOTAL", label: "Итог", formula: "" },
  ] };
  await gateway.saveReportLayout({ ...query, rows: layout.rows });
  const close = vi.fn();
  render(<WorkspaceSettingsDialog gateway={gateway} organizations={(await gateway.listOrganizations()).organizations}
    initialOrganizationId={query.organization_id} initialReportType={report_type}
    onOrganizationsChange={vi.fn()} onApply={vi.fn()} onClose={close} />);
  await user.click((await screen.findAllByText(/Изображение, показатели и формулы/))[0]!);
  await user.click(screen.getAllByText("Библиотека формул — открыть шпаргалку")[0]!);
  await user.selectOptions(screen.getAllByLabelText("Первый показатель / приход")[0]!, "INPUT");
  const card = screen.getByText("Сумма внесённых значений с начала года").closest("article")!;
  expect(within(card).getByRole("button", { name: "Вставить в показатель" })).toBeDisabled();
  await user.selectOptions(screen.getAllByLabelText("Показатель результата")[0]!, "INPUT");
  expect(within(card).getByRole("button", { name: "Вставить в показатель" })).toBeDisabled();
  await user.selectOptions(screen.getAllByLabelText("Показатель результата")[0]!, "TOTAL");
  await user.click(within(card).getByRole("button", { name: "Копировать формулу" }));
  expect(clipboard).toHaveBeenCalledWith("=CUMSUM(INPUT)");
  await user.click(within(card).getByRole("button", { name: "Вставить в показатель" }));
  expect(close).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toBeVisible();
  expect(screen.getAllByLabelText("Формула")[1]).toHaveValue("=CUMSUM(INPUT)");
  clipboard.mockRejectedValueOnce(new Error("unavailable"));
  await user.click(within(card).getByRole("button", { name: "Копировать формулу" }));
  expect(await screen.findByText(/Буфер обмена недоступен/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Применить настройки" }));
  expect((await gateway.getReportLayout(query)).rows[0]!.configuration!.indicators[1]!.formula).toBe("=CUMSUM(INPUT)");
});
