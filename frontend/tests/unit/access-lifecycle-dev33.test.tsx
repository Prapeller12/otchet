import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ResponsibleUsers } from "../../src/features/access/ResponsibleUsers";
import { AuthorizationDialog } from "../../src/features/access/AuthorizationDialog";
import { DemoGateway } from "../../src/shared/api/demo-gateway";
import type { ReportSigner } from "../../src/shared/api/application-gateway";

const admin: ReportSigner = { id: "a", display_name: "Анна", role: "admin", key_fingerprint: "key", created_at: "2026-01-01" };
const reviewer: ReportSigner = { ...admin, id: "r", display_name: "Иван", role: "reviewer" };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("does not offer revoked signers for write authorization", async () => {
  const gateway = Object.assign(new DemoGateway(), { listReportSigners: async () => [admin, { ...reviewer, revoked: true }] });
  render(<AuthorizationDialog gateway={gateway} pending={{ title: "Сохранить", adminOnly: false, execute: vi.fn(), resolve: vi.fn(), reject: vi.fn() }} onClose={vi.fn()} />);
  await screen.findByRole("option", { name: /Анна/ });
  expect(screen.queryByRole("option", { name: /Иван/ })).toBeNull();
});

it("requires explicit revoke confirmation and never offers it for sole administrator", async () => {
  const manage = vi.fn(async () => ({ users: [admin, { ...reviewer, revoked: true }], administrator_changed: false }));
  const list = vi.fn().mockResolvedValueOnce([admin, reviewer]).mockResolvedValue([admin, { ...reviewer, revoked: true }]);
  const gateway = Object.assign(new DemoGateway(), { listReportSigners: list, manageReportSigner: manage });
  const user = userEvent.setup();
  render(<ResponsibleUsers gateway={gateway} />);
  const adminRow = (await screen.findByText("Анна")).closest("tr")!;
  expect(within(adminRow).queryByRole("button", { name: "Отозвать доступ" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Отозвать доступ" }));
  expect(manage).not.toHaveBeenCalled();
  expect(screen.getByText(/Старые подписи сохранятся/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Продолжить с подтверждением администратора" }));
  await waitFor(() => expect(manage).toHaveBeenCalledWith({ action: "revoke", signer_id: "r" }));
  expect(await screen.findByText(/Проверяющий · доступ отозван/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Отозвать доступ" })).toBeNull();
});

it("checks repeated PIN and sends both old and new code only for the selected profile", async () => {
  const manage = vi.fn(async () => ({ users: [admin, reviewer], administrator_changed: false }));
  const gateway = Object.assign(new DemoGateway(), { listReportSigners: async () => [admin, reviewer], manageReportSigner: manage });
  const user = userEvent.setup();
  render(<ResponsibleUsers gateway={gateway} />);
  const row = (await screen.findByText("Иван")).closest("tr")!;
  await user.click(within(row).getByRole("button", { name: "Сменить код" }));
  await user.type(screen.getByLabelText("Действующий код выбранного пользователя"), "old-code");
  await user.type(screen.getByLabelText("Новый код"), "new-code");
  await user.type(screen.getByLabelText("Повтор нового кода"), "different");
  await user.click(screen.getByRole("button", { name: "Продолжить с подтверждением администратора" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Новые коды не совпадают");
  expect(manage).not.toHaveBeenCalled();
  await user.clear(screen.getByLabelText("Повтор нового кода"));
  await user.type(screen.getByLabelText("Повтор нового кода"), "new-code");
  await user.click(screen.getByRole("button", { name: "Продолжить с подтверждением администратора" }));
  await waitFor(() => expect(manage).toHaveBeenCalledWith({ action: "change_pin", signer_id: "r", current_pin: "old-code", new_pin: "new-code" }));
});
