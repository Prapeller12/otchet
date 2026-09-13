import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceSettingsDialog } from "../../src/features/workspace-settings/WorkspaceSettingsDialog";
import { DemoGateway } from "../../src/shared/api/demo-gateway";

afterEach(cleanup);
it("edits summary names while preserving indicator codes and formulas", async () => {
  const gateway = new DemoGateway();
  const layout = {
    report_type: "DAILY_MOVEMENT" as const, organization_id: "1",
    templates: [{ id: "warehouse", label: "Склад и комплектность", group_kind: "WAREHOUSE_READINESS", repeatable: false }],
    rows: [{ id: "4", template_group_id: "warehouse", party_name: "Головная площадка", position_name: "Склад и комплектность", configuration: {
      category: "UNSPECIFIED", image: "", norm: "", opening: "",
      indicators: [{ code: "WRK_DAILY_READY_SETS", label: "Готовые комплекты", formula: "" }],
    } }],
  };
  vi.spyOn(gateway, "getReportLayout").mockResolvedValue(layout);
  const save = vi.spyOn(gateway, "saveReportLayout").mockResolvedValue(layout);
  const user = userEvent.setup();
  render(<WorkspaceSettingsDialog gateway={gateway} organizations={[{ id: "1", name: "Головная площадка", kind: "HEAD" }]} initialOrganizationId="1" initialReportType="DAILY_MOVEMENT" onOrganizationsChange={() => {}} onApply={() => {}} onClose={() => {}} />);
  const position = await screen.findByLabelText("Позиция");
  await user.clear(position); await user.type(position, "Склад готовой продукции");
  const indicator = screen.getByLabelText("Название показателя");
  await user.clear(indicator); await user.type(indicator, "Комплектность изделия");
  expect(screen.queryByRole("button", { name: "Копировать позицию" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Применить настройки" }));
  await waitFor(() => expect(save).toHaveBeenCalled());
  expect(save.mock.calls[0]![0].rows[0]).toMatchObject({ position_name: "Склад готовой продукции", configuration: { indicators: [{ code: "WRK_DAILY_READY_SETS", label: "Комплектность изделия", formula: "" }] } });
});
