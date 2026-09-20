import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../src/app/App";
import { ApplicationGatewayProvider } from "../../src/app/providers/ApplicationGatewayProvider";
import { AccessGate } from "../../src/features/access/AccessGate";
import { AuthorizationDialog } from "../../src/features/access/AuthorizationDialog";
import { Onboarding } from "../../src/features/access/Onboarding";
import { DemoGateway } from "../../src/shared/api/demo-gateway";
import type { AccessStatus, ReportSigner } from "../../src/shared/api/application-gateway";

const admin: ReportSigner = { id: "a", display_name: "Анна", role: "admin", key_fingerprint: "key", created_at: "2026-01-01" };
const reviewer: ReportSigner = { ...admin, id: "r", display_name: "Иван", role: "reviewer" };
const manager: ReportSigner = { ...admin, id: "m", display_name: "Ольга", role: "project_manager" };
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

it("does not query reports before unlocking; rejected code keeps the gate closed", async () => {
  const unlock = vi.fn().mockRejectedValueOnce(new Error("Неверный код")).mockResolvedValue({ state: "ready", users: [admin] });
  const gateway = Object.assign(new DemoGateway(), {
    getAccessStatus: vi.fn(async (): Promise<AccessStatus> => ({ state: "locked", users: [admin] })),
    unlockAccess: unlock,
  });
  const organizations = vi.spyOn(gateway, "listOrganizations");
  render(<ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider>);
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Код доступа"), "wrong-code");
  expect(organizations).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Открыть отчёты" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Неверный код");
  expect(screen.getByLabelText("Код доступа")).toHaveValue("");
  expect(organizations).not.toHaveBeenCalled();
  await user.type(screen.getByLabelText("Код доступа"), "right-code");
  await user.click(screen.getByRole("button", { name: "Открыть отчёты" }));
  await screen.findByText("Ежедневное движение и остатки");
  expect(organizations).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: "Настроить рабочее поле" })).toBeNull();
});

it("first administrator setup checks repeated code and sends no duplicate role", async () => {
  const setup = vi.fn(async (): Promise<AccessStatus> => ({ state: "ready", users: [admin] }));
  const gateway = Object.assign(new DemoGateway(), { getAccessStatus: async (): Promise<AccessStatus> => ({ state: "setup", users: [] }), setupAccess: setup });
  render(<AccessGate gateway={gateway}><p>Рабочая форма</p></AccessGate>);
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Имя администратора"), "Анна");
  await user.type(screen.getByLabelText("Код администратора (от 6 символов)"), "secret-code");
  await user.type(screen.getByLabelText("Повторите код"), "different");
  await user.click(screen.getByRole("button", { name: "Создать администратора и начать" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Коды не совпадают");
  expect(setup).not.toHaveBeenCalled();
  await user.clear(screen.getByLabelText("Повторите код"));
  await user.type(screen.getByLabelText("Повторите код"), "secret-code");
  await user.click(screen.getByRole("button", { name: "Создать администратора и начать" }));
  expect(await screen.findByText("Рабочая форма")).toBeVisible();
  expect(setup).toHaveBeenCalledWith({ display_name: "Анна", pin: "secret-code" });
});

it("write authorization excludes project managers and retries a wrong code without completing the write", async () => {
  const gateway = Object.assign(new DemoGateway(), { listReportSigners: async () => [admin, reviewer, manager] });
  const execute = vi.fn().mockRejectedValueOnce(new Error("Неверный код")).mockResolvedValue({ saved: true });
  const resolve = vi.fn(); const close = vi.fn();
  render(<AuthorizationDialog gateway={gateway} pending={{ title: "Сохранить изменения отчёта", adminOnly: false, execute, resolve, reject: vi.fn() }} onClose={close} />);
  const user = userEvent.setup();
  await screen.findByRole("option", { name: /Иван/ });
  expect(screen.queryByRole("option", { name: /Ольга/ })).toBeNull();
  await user.selectOptions(screen.getByLabelText("Ответственное лицо"), "r");
  await user.type(screen.getByLabelText("Код подтверждения"), "wrong-code");
  await user.click(screen.getByRole("button", { name: "Подтвердить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Неверный код");
  expect(resolve).not.toHaveBeenCalled(); expect(close).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Код подтверждения")).toHaveValue("");
  await user.type(screen.getByLabelText("Код подтверждения"), "right-code");
  await user.click(screen.getByRole("button", { name: "Подтвердить" }));
  await waitFor(() => expect(resolve).toHaveBeenCalledWith({ saved: true }));
  expect(execute).toHaveBeenLastCalledWith({ signer_id: "r", pin: "right-code" });
});

it("onboarding explains blank versus zero and code on save in three short steps", async () => {
  const close = vi.fn(); const user = userEvent.setup();
  render(<Onboarding onClose={close} />);
  await user.click(screen.getByRole("button", { name: "Далее" }));
  expect(screen.getByText(/Ноль означает/)).toHaveTextContent("Пустая ячейка");
  await user.click(screen.getByRole("button", { name: "Далее" }));
  expect(screen.getByText(/Проверяющий или администратор/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Начать заполнение" }));
  expect(close).toHaveBeenCalledTimes(1);
});

it("does not offer database-unlock enrollment for a project manager", async () => {
  const { ResponsibleUsers } = await import("../../src/features/access/ResponsibleUsers");
  const gateway = Object.assign(new DemoGateway(), { listReportSigners: async () => [{ ...admin, can_unlock: true }, { ...manager, can_unlock: false }] });
  render(<ResponsibleUsers gateway={gateway} />);
  expect(await screen.findByText("После открытия ответственным")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Разрешить вход" })).toBeNull();
  await userEvent.setup().selectOptions(screen.getByLabelText("Роль"), "project_manager");
  expect(screen.getByText(/Заполняет отчёт после открытия программы/)).toBeVisible();
});
