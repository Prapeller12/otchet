import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DemoGateway } from "../../src/shared/api/demo-gateway";
import { MonthlyReportActions } from "../../src/widgets/report-matrix/MonthlyReportActions";
import { useState } from "react";

afterEach(cleanup);
const query = { report_type: "DAILY_MOVEMENT" as const, organization_id: "1", year: 2024 };

const signer = { id: "user-1", display_name: "Иванов", role: "admin" as const, key_fingerprint: "ABCD0123456789EF", created_at: "2024-02-29" };
const signerApi = { listReportSigners: vi.fn(async () => [signer]), createReportSigner: vi.fn(async () => signer) };

it("shares the selected reporting month with printing and stock-week verification", async () => {
  const pdf = vi.fn(async () => ({ cancelled: true }));
  const status = vi.fn(async () => ({ status: "UNVERIFIED" as const, snapshot_sha256: "a" }));
  const gateway = Object.assign(new DemoGateway(), signerApi, { getReportVerification: status, verifyReport: vi.fn(), exportPdf: pdf });
  function Harness() {
    const [month, setMonth] = useState("2024-02");
    return <MonthlyReportActions gateway={gateway} query={{ ...query, report_type: "SUBSIDIARY" }} revision="r1" blocked={false} controlledMonth={month} onMonthChange={setMonth} weeks={{ "2024-02": "2024-02-05", "2024-03": "2024-03-04" }} />;
  }
  render(<Harness />);
  const user = userEvent.setup();
  expect(screen.getByLabelText("Месяц печати и проверки")).toHaveValue("2");
  await user.click(screen.getByRole("button", { name: "Печать / PDF А4" }));
  expect(pdf).toHaveBeenLastCalledWith(expect.objectContaining({ month: 2, week_start: "2024-02-05" }));
  await user.selectOptions(screen.getByLabelText("Месяц печати и проверки"), "3");
  await waitFor(() => expect(status).toHaveBeenLastCalledWith(expect.objectContaining({ month: 3, week_start: "2024-03-04" })));
});

it("exports the selected month and requires an explicit attestation of the exact snapshot", async () => {
  const user = userEvent.setup();
  const status = vi.fn(async () => ({ status: "UNVERIFIED" as const, snapshot_sha256: "abc" }));
  const verify = vi.fn(async () => ({ status: "VERIFIED" as const, snapshot_sha256: "abc", signer_name: "Иванов", signed_at: "2024-02-29" }));
  const pdf = vi.fn(async () => ({ cancelled: false, file_name: "report.pdf" }));
  const gateway = Object.assign(new DemoGateway(), signerApi, { getReportVerification: status, verifyReport: verify, exportPdf: pdf });
  const view = render(<MonthlyReportActions gateway={gateway} query={query} revision="db-1" blocked={false} />);
  await user.selectOptions(screen.getByLabelText("Месяц печати и проверки"), "2");
  await user.click(screen.getByRole("button", { name: "Печать / PDF А4" }));
  expect(pdf).toHaveBeenCalledWith({ ...query, month: 2, expected_revision: "db-1" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Подтвердить данные" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "Подтвердить данные" }));
  expect(screen.getByRole("button", { name: "Подтверждаю верность данных" })).toBeDisabled();
  await user.type(await screen.findByLabelText("Код проверяющего или администратора"), "test-pin");
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Подтверждаю верность данных" }));
  expect(verify).toHaveBeenCalledWith({ ...query, month: 2, expected_revision: "db-1", signer_id: "user-1", pin: "test-pin", confirmed: true, snapshot_sha256: "abc" });
  expect(await screen.findByText(/Подтверждено: Иванов/)).toBeVisible();
  view.rerender(<MonthlyReportActions gateway={gateway} query={query} revision="db-2" blocked={true} />);
  expect(screen.queryByText(/Подтверждено: Иванов/)).toBeNull();
  expect(screen.getByRole("button", { name: "Печать / PDF А4" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Подтвердить данные" })).toBeDisabled();
});

it("keeps the confirmation open when the backend rejects a stale snapshot", async () => {
  const gateway = Object.assign(new DemoGateway(), signerApi, {
    getReportVerification: vi.fn(async () => ({ status: "UNVERIFIED" as const, snapshot_sha256: "old" })),
    verifyReport: vi.fn(async () => { throw new Error("Данные изменились"); }),
    exportPdf: vi.fn(async () => ({ cancelled: true })),
  });
  render(<MonthlyReportActions gateway={gateway} query={query} revision="db-1" blocked={false} />);
  const user = userEvent.setup();
  await waitFor(() => expect(screen.getByRole("button", { name: "Подтвердить данные" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "Подтвердить данные" }));
  await user.type(await screen.findByLabelText("Код проверяющего или администратора"), "test-pin");
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Подтверждаю верность данных" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Данные изменились");
  expect(screen.getByRole("dialog")).toBeVisible();
  expect(screen.getByLabelText("Код проверяющего или администратора")).toHaveValue("");
});

it("directs users without a reviewer to the administrator instead of creating keys in entry", async () => {
  const createReportSigner = vi.fn();
  const gateway = Object.assign(new DemoGateway(), {
    getReportVerification: vi.fn(async () => ({ status: "UNVERIFIED" as const, snapshot_sha256: "abc" })),
    listReportSigners: vi.fn(async () => []), createReportSigner, verifyReport: vi.fn(),
    exportPdf: vi.fn(async () => ({ cancelled: true })),
  });
  render(<MonthlyReportActions gateway={gateway} query={query} revision="db-1" blocked={false} />);
  const user = userEvent.setup();
  await waitFor(() => expect(screen.getByRole("button", { name: "Подтвердить данные" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "Подтвердить данные" }));
  expect(await screen.findByText(/Нет проверяющих/)).toBeVisible();
  expect(screen.queryByLabelText("ФИО пользователя")).toBeNull();
  expect(screen.queryByRole("button", { name: "Создать ключ пользователя" })).toBeNull();
  expect(createReportSigner).not.toHaveBeenCalled();
});

it("does not offer project managers as reviewers or create new users in the signing flow", async () => {
  const gateway = Object.assign(new DemoGateway(), signerApi, {
    getReportVerification: vi.fn(async () => ({ status: "UNVERIFIED" as const, snapshot_sha256: "abc" })),
    listReportSigners: vi.fn(async () => [signer, { ...signer, id: "manager", display_name: "Руководитель", role: "project_manager" }]),
    verifyReport: vi.fn(), exportPdf: vi.fn(),
  });
  render(<MonthlyReportActions gateway={gateway} query={query} revision="db-1" blocked={false} />);
  const user = userEvent.setup();
  await waitFor(() => expect(screen.getByRole("button", { name: "Подтвердить данные" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "Подтвердить данные" }));
  await screen.findByLabelText("Код проверяющего или администратора");
  expect(screen.queryByRole("option", { name: /Руководитель/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Добавить пользователя" })).toBeNull();
});
