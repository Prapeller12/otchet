import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { SubsidiaryDetailEditor } from "../../src/features/workspace-settings/SubsidiaryDetailEditor";
import { SubsidiaryControls } from "../../src/widgets/report-matrix/SubsidiaryControls";
import type { ReportLayoutRow, ReportMatrixContract, ApplicationGateway } from "../../src/shared/api/application-gateway";

afterEach(cleanup);
it("adds and archives a manufacturer without duplicating or deleting the detail", async () => {
  const user = userEvent.setup(); const remove = vi.fn();
  function Harness() {
    const [row, setRow] = useState<ReportLayoutRow>({ id: "1", template_group_id: "supplier", party_name: "Завод А", position_name: "Деталь", configuration: { category: "DSE", norm: "4", image: "", opening: "", indicators: [] } });
    return <SubsidiaryDetailEditor row={row} onBusy={vi.fn()} onChange={setRow} onRemove={remove} onMove={vi.fn()} disabled={false} />;
  }
  render(<Harness />);
  await user.click(screen.getByRole("button", { name: "+ Добавить производителя" }));
  expect(screen.getAllByLabelText("Производитель")).toHaveLength(2);
  const added = screen.getAllByLabelText("Производитель")[1]!;
  expect(added).toHaveValue("");
  expect(added).toHaveAttribute("placeholder", "Введите производителя");
  await user.type(added, "АО Завод");
  expect(added).toHaveValue("АО Завод");
  expect(screen.getAllByLabelText("Наименование")).toHaveLength(1);
  await user.click(screen.getAllByRole("button", { name: "Убрать производителя" })[0]!);
  expect(remove).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Восстановить производителя" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Восстановить производителя" }));
  expect(screen.getAllByRole("button", { name: "Убрать производителя" })).toHaveLength(2);
});

it("automatically saves the monthly plan on blur without technical labels", async () => {
  const user = userEvent.setup(); const change = vi.fn(); const week = vi.fn(); const busy = vi.fn();
  const matrix: ReportMatrixContract = { title: "Дочерние общества", subtitle: "", source_notice: "", form_status: "WORKING_REFERENCE", left_columns: [], rows: [], capabilities: { save: {enabled: true}, import: {enabled: true}, export: {enabled: true} }, navigation: {enter_direction: "down"}, report_type: "SUBSIDIARY", organization_id: "1", year: 2026, matrix_revision: "r1", presentation: { plans: { "2026-08": "500" } }, time_columns: [{ id: "2026-09-01", group_label: "2026-09", kind: "USED", width: 64, label: "01–06" }, { id: "2026-09-28", group_label: "2026-09", kind: "USED", width: 64, label: "28–30" }] };
  const save = vi.fn().mockResolvedValue({});
  const gateway = { saveReportPresentation: save, getReportMatrix: vi.fn().mockResolvedValue(matrix) } as unknown as ApplicationGateway;
  render(<SubsidiaryControls matrix={matrix} gateway={gateway} blocked={false} month="2026-09" onMonth={vi.fn()} week="" onWeek={week} onChange={change} onBusy={busy} />);
  await user.type(screen.getByLabelText("План выпуска, шт."), "1000");
  await user.tab();
  await waitFor(() => expect(change).toHaveBeenCalledWith(matrix));
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ expected_revision: "r1", plans: { "2026-08": "500", "2026-09": "1000" } }));
  expect(screen.queryByText(/C6/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Остаток на конец недели")).not.toBeInTheDocument();
});
