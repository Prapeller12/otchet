import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceSettingsDialog } from "../../src/features/workspace-settings/WorkspaceSettingsDialog";
import { DemoGateway } from "../../src/shared/api/demo-gateway";

afterEach(cleanup);

it("adds a daily component consumption group with the source worksheet row order", async () => {
  const gateway = new DemoGateway();
  const organizations = (await gateway.listOrganizations()).organizations;
  const user = userEvent.setup();
  render(<WorkspaceSettingsDialog gateway={gateway} organizations={organizations}
    initialOrganizationId={organizations[0]!.id} initialReportType="DAILY_MOVEMENT"
    onOrganizationsChange={vi.fn()} onApply={vi.fn()} onClose={vi.fn()} />);
  const button = await screen.findByRole("button", { name: "+ Расход составной части" });
  const before = screen.getAllByLabelText("Категория позиции").length;
  await user.click(button);
  expect(screen.getAllByLabelText("Категория позиции")).toHaveLength(before + 1);
  expect(screen.getAllByLabelText("Категория позиции").at(-1)).toHaveValue("PART");
});

it("offers subsidiary report links in head settings without formula entry", async () => {
  const gateway = new DemoGateway();
  const organizations = (await gateway.listOrganizations()).organizations;
  const added = await gateway.createOrganization("Общество А");
  render(<WorkspaceSettingsDialog gateway={gateway} organizations={[...organizations, added.organization]}
    initialOrganizationId={organizations[0]!.id} initialReportType="HEAD_SITE"
    onOrganizationsChange={vi.fn()} onApply={vi.fn()} onClose={vi.fn()} />);
  const user = userEvent.setup();
  const lists = await screen.findAllByLabelText("Связать отчёт");
  await user.selectOptions(lists[0]!, added.organization.id);
  if (lists.length > 1) {
    expect(screen.getAllByRole("option", { name: "Общество А — уже связан" })[0]).toBeDisabled();
  }
  expect(screen.queryByLabelText("Формула")).not.toBeInTheDocument();
});
