import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DemoGateway } from "../../src/shared/api/demo-gateway";
import { MonthlyReportActions } from "../../src/widgets/report-matrix/MonthlyReportActions";

afterEach(cleanup);
const query = { report_type: "DAILY_MOVEMENT" as const, organization_id: "1", year: 2024 };

it("exports the selected month and requires an explicit attestation of the exact snapshot", async () => {
  const user = userEvent.setup();
  const status = vi.fn(async () => ({ status: "UNVERIFIED" as const, snapshot_sha256: "abc" }));
  const verify = vi.fn(async () => ({ status: "VERIFIED" as const, snapshot_sha256: "abc", signer_name: "Иванов", signed_at: "2024-02-29" }));
  const pdf = vi.fn(async () => ({ cancelled: false, file_name: "report.pdf" }));
  const gateway = Object.assign(new DemoGateway(), { getReportVerification: status, verifyReport: verify, exportPdf: pdf });
  const view = render(<MonthlyReportActions gateway={gateway} query={query} revision="db-1" blocked={false} />);
  await user.selectOptions(screen.getByLabelText("Месяц печати и проверки"), "2");
  await user.click(screen.getByRole("button", { name: "Печать / PDF А4" }));
  expect(pdf).toHaveBeenCalledWith({ ...query, month: 2, expected_revision: "db-1" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Подтвердить данные" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "Подтвердить данные" }));
  expect(screen.getByRole("button", { name: "Подтверждаю верность данных" })).toBeDisabled();
  await user.type(screen.getByLabelText("ФИО руководителя"), "Иванов");
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Подтверждаю верность данных" }));
  expect(verify).toHaveBeenCalledWith({ ...query, month: 2, expected_revision: "db-1", signer_name: "Иванов", confirmed: true, snapshot_sha256: "abc" });
  expect(await screen.findByText(/Подтверждено: Иванов/)).toBeVisible();
  view.rerender(<MonthlyReportActions gateway={gateway} query={query} revision="db-2" blocked={true} />);
  expect(screen.queryByText(/Подтверждено: Иванов/)).toBeNull();
  expect(screen.getByRole("button", { name: "Печать / PDF А4" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Подтвердить данные" })).toBeDisabled();
});

it("keeps the confirmation open when the backend rejects a stale snapshot", async () => {
  const gateway = Object.assign(new DemoGateway(), {
    getReportVerification: vi.fn(async () => ({ status: "UNVERIFIED" as const, snapshot_sha256: "old" })),
    verifyReport: vi.fn(async () => { throw new Error("Данные изменились"); }),
    exportPdf: vi.fn(async () => ({ cancelled: true })),
  });
  render(<MonthlyReportActions gateway={gateway} query={query} revision="db-1" blocked={false} />);
  const user = userEvent.setup();
  await waitFor(() => expect(screen.getByRole("button", { name: "Подтвердить данные" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "Подтвердить данные" }));
  await user.type(screen.getByLabelText("ФИО руководителя"), "Иванов");
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Подтверждаю верность данных" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Данные изменились");
  expect(screen.getByRole("dialog")).toBeVisible();
});
