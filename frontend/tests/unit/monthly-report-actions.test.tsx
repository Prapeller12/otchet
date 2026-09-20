import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DemoGateway } from "../../src/shared/api/demo-gateway";
import { MonthlyReportActions } from "../../src/widgets/report-matrix/MonthlyReportActions";

afterEach(cleanup);
const query = { report_type: "SUBSIDIARY" as const, organization_id: "1", year: 2024 };

it("shows status for the selected month/week without a second month selector or confirmation button", async () => {
  const status = vi.fn(async () => ({ status: "UNVERIFIED" as const, snapshot_sha256: "a" }));
  const gateway = Object.assign(new DemoGateway(), { getReportVerification: status });
  const view = render(<MonthlyReportActions gateway={gateway} query={query} revision="r1" blocked={false} controlledMonth="2024-02" weeks={{ "2024-02": "2024-02-05" }} />);
  await waitFor(() => expect(status).toHaveBeenLastCalledWith({ ...query, month: 2, week_start: "2024-02-05", expected_revision: "r1" }));
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.queryByRole("combobox")).toBeNull();
  view.rerender(<MonthlyReportActions gateway={gateway} query={query} revision="r1" blocked={false} controlledMonth="2024-03" weeks={{ "2024-03": "2024-03-04" }} />);
  await waitFor(() => expect(status).toHaveBeenLastCalledWith(expect.objectContaining({ month: 3, week_start: "2024-03-04" })));
});

it("refreshes confirmation after printing and hides old status when there are drafts", async () => {
  const status = vi.fn().mockResolvedValueOnce({ status: "UNVERIFIED", snapshot_sha256: "abc" }).mockResolvedValue({ status: "VERIFIED", snapshot_sha256: "abc", signer_name: "Иванов", signed_at: "2024-02-29", key_fingerprint: "ABCD" });
  const gateway = Object.assign(new DemoGateway(), { getReportVerification: status });
  const props = { gateway, query, revision: "r1", blocked: false, controlledMonth: "2024-02" };
  const view = render(<MonthlyReportActions {...props} />);
  await waitFor(() => expect(status).toHaveBeenCalledTimes(1));
  view.rerender(<MonthlyReportActions {...props} refresh={1} />);
  expect(await screen.findByText(/Подтверждено: Иванов/)).toBeVisible();
  view.rerender(<MonthlyReportActions {...props} refresh={1} blocked />);
  expect(screen.queryByText(/Подтверждено: Иванов/)).toBeNull();
  expect(screen.getByRole("status")).toHaveTextContent("Сохраните их перед печатью");
});

it("does not display a late verification result for a different selected month", async () => {
  let resolve!: (value: { status: "VERIFIED"; snapshot_sha256: string; signer_name: string }) => void;
  const status = vi.fn().mockReturnValueOnce(new Promise(done => { resolve = done; })).mockResolvedValue({ status: "UNVERIFIED", snapshot_sha256: "new" });
  const gateway = Object.assign(new DemoGateway(), { getReportVerification: status });
  const view = render(<MonthlyReportActions gateway={gateway} query={query} revision="r1" blocked={false} controlledMonth="2024-02" />);
  view.rerender(<MonthlyReportActions gateway={gateway} query={query} revision="r1" blocked={false} controlledMonth="2024-03" />);
  resolve({ status: "VERIFIED", snapshot_sha256: "old", signer_name: "Устаревший" });
  await waitFor(() => expect(status).toHaveBeenCalledTimes(2));
  expect(screen.queryByText(/Устаревший/)).toBeNull();
});
