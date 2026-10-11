import { StrictMode, useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ApplicationGatewayProvider } from "../../src/app/providers/ApplicationGatewayProvider";
import { App } from "../../src/app/App";
import { BlankReportsDialog } from "../../src/features/blank-reports/BlankReportsDialog";
import { DemoGateway } from "../../src/shared/api/demo-gateway";
import type { BlankReportProfiles, BlankReportProfilesResult, ReportMatrixQuery } from "../../src/shared/api/application-gateway";
import { sourceMatrix } from "../fixtures/source-matrix";

afterEach(cleanup);
const profiles: BlankReportProfiles = { profile_set_id: "synthetic-fixture", initial_period: "2027-09-01", quantity_policy: "DATA_NOT_PROVIDED", organizations: [{ template_id: "head", label: "КГЭ", report_type: "HEAD_SITE", position_count: 1, distinct_manufacturer_count: 1, supplier_row_count: 1, display_row_count: 1 }] };
it("prevents closing a blank setup throughout creation and opening the resulting reports", async () => {
  let finish: ((value: BlankReportProfilesResult) => void) | undefined;
  let finishOpening: (() => void) | undefined;
  const create = vi.fn(() => new Promise<BlankReportProfilesResult>(resolve => { finish = resolve; })); const completed = vi.fn(() => new Promise<void>(resolve => { finishOpening = resolve; })); const close = vi.fn();
  const gateway = Object.assign(new DemoGateway(), { listBlankReportProfiles: vi.fn(async () => profiles), createBlankReportProfiles: create });
  render(<ApplicationGatewayProvider gateway={gateway}><BlankReportsDialog onClose={close} onCompleted={completed} /></ApplicationGatewayProvider>);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Создать или открыть пять заготовок" }));
  expect(screen.getByRole("button", { name: "Закрыть заготовки" })).toBeDisabled();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" }); expect(close).not.toHaveBeenCalled();
  finish!({ created: true, initial_period: "2027-09-01", organizations: [{ template_id: "head", label: "КГЭ", organization_id: "2", report_type: "HEAD_SITE" }] });
  await waitFor(() => expect(completed).toHaveBeenCalledWith({ organization_id: "2", report_type: "HEAD_SITE", year: 2027, month: 9 }));
  expect(screen.getByRole("button", { name: "Закрыть заготовки" })).toBeDisabled(); fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" }); expect(close).not.toHaveBeenCalled();
  finishOpening!();
  await waitFor(() => expect(screen.getByRole("button", { name: "Закрыть заготовки" })).toBeEnabled()); fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" }); expect(close).toHaveBeenCalledTimes(1);
});
it("loops keyboard focus inside the blank dialog and restores its launcher after Escape", async () => {
  const gateway = Object.assign(new DemoGateway(), { listBlankReportProfiles: vi.fn(async () => profiles), createBlankReportProfiles: vi.fn() });
  function Workspace() {
    const [open, setOpen] = useState(false);
    return <><button onClick={() => setOpen(true)}>Открыть заготовки</button>{open && <BlankReportsDialog onClose={() => setOpen(false)} onCompleted={() => {}} />}</>;
  }
  render(<StrictMode><ApplicationGatewayProvider gateway={gateway}><Workspace /></ApplicationGatewayProvider></StrictMode>); const user = userEvent.setup();
  const launcher = screen.getByRole("button", { name: "Открыть заготовки" }); await user.click(launcher);
  const create = await screen.findByRole("button", { name: "Создать или открыть пять заготовок" }); await waitFor(() => expect(create).toBeEnabled());
  expect(screen.getByRole("button", { name: "Закрыть заготовки" })).toHaveFocus(); await user.tab({ shift: true }); expect(create).toHaveFocus();
  await user.tab(); expect(screen.getByRole("button", { name: "Закрыть заготовки" })).toHaveFocus(); await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull(); expect(launcher).toHaveFocus();
});
it("opens new KGE head sheets in September 2027 and exposes no earlier start period", async () => {
  const organizations = [{ id: "old", name: "Прежняя организация", kind: "HEAD" as const }, { id: "2", name: "КГЭ", kind: "HEAD" as const, start_period: "2027-09-01" }, { id: "3", name: "Молот", kind: "SUBSIDIARY" as const, start_period: "2027-09-01" }];
  const read = vi.fn(async (query: ReportMatrixQuery) => {
    const matrix = sourceMatrix(); matrix.organization_id = query.organization_id; matrix.report_type = query.report_type; matrix.head_site = query.report_type === "HEAD_SITE"; matrix.year = query.year ?? 2027;
    matrix.time_columns = matrix.time_columns.map(column => ({ ...column, id: column.id.replace("2026-09", `${matrix.year}-09`), group_label: `${matrix.year}-09` }));
    return matrix;
  });
  const gateway = Object.assign(new DemoGateway(), { listOrganizations: vi.fn(async () => ({ organizations })), getReportMatrix: read });
  render(<ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider>);
  await waitFor(() => expect(screen.getByLabelText("Организация")).toHaveValue("2")); await waitFor(() => expect(screen.getByLabelText("Год отчёта")).toHaveValue("2027"));
  expect(screen.getByLabelText("Год отчёта").querySelector('option[value="2026"]')).toBeNull(); expect(screen.getByLabelText("Месяц")).toHaveValue("2027-09"); expect(screen.getByRole("button", { name: "Головная площадка" })).toHaveAttribute("aria-current", "page");
  await userEvent.setup().selectOptions(screen.getByLabelText("Организация"), "3"); await waitFor(() => expect(screen.getByRole("button", { name: "Дочерние общества" })).toHaveAttribute("aria-current", "page"));
  expect(read).toHaveBeenLastCalledWith(expect.objectContaining({ organization_id: "3", report_type: "SUBSIDIARY", year: 2027 }));
  await userEvent.setup().click(screen.getByRole("button", { name: "Головная площадка" }));
  await waitFor(() => expect(read).toHaveBeenLastCalledWith(expect.objectContaining({ organization_id: "2", report_type: "HEAD_SITE", year: 2027 })));
  await userEvent.setup().click(screen.getByRole("button", { name: "Дочерние общества" }));
  await waitFor(() => expect(read).toHaveBeenLastCalledWith(expect.objectContaining({ organization_id: "3", report_type: "SUBSIDIARY", year: 2027 })));
  await userEvent.setup().click(screen.getByRole("button", { name: "Ежедневный отчёт" }));
  await waitFor(() => expect(read).toHaveBeenLastCalledWith(expect.objectContaining({ organization_id: "3", report_type: "DAILY_MOVEMENT", year: 2027 })));
});
it("ends administrator access before returning to the created blank reports", async () => {
  const organizations = [{ id: "2", name: "КГЭ", kind: "HEAD" as const, start_period: "2027-09-01" }];
  const end = vi.fn(async () => {});
  const gateway = Object.assign(new DemoGateway(), {
    listOrganizations: vi.fn(async () => ({ organizations })), listBlankReportProfiles: vi.fn(async () => profiles),
    createBlankReportProfiles: vi.fn(async () => ({ created: true, initial_period: "2027-09-01", organizations: [{ template_id: "head", label: "КГЭ", organization_id: "2", report_type: "HEAD_SITE" as const }] })),
    endAdministration: end,
  });
  render(<ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider>);
  const user = userEvent.setup(); await user.click(screen.getByRole("button", { name: "Администратор" }));
  await user.click(screen.getByRole("button", { name: "Создать заготовки с сентября 2027" }));
  await user.click(await screen.findByRole("button", { name: "Создать или открыть пять заготовок" }));
  await waitFor(() => expect(end).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Заготовки отчётов с сентября 2027 года" })).toBeNull());
  expect(screen.getByRole("button", { name: "Администратор" })).toBeVisible();
});
