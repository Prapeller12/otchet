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

it("write authorization includes project managers and retries a wrong code without completing the write", async () => {
  const gateway = Object.assign(new DemoGateway(), { listReportSigners: async () => [admin, reviewer, manager] });
  const execute = vi.fn().mockRejectedValueOnce(new Error("Неверный код")).mockResolvedValue({ saved: true });
  const resolve = vi.fn(); const close = vi.fn();
  render(<AuthorizationDialog gateway={gateway} pending={{ title: "Сохранить изменения отчёта", adminOnly: false, execute, resolve, reject: vi.fn() }} onClose={close} />);
  const user = userEvent.setup();
  await screen.findByRole("option", { name: /Иван/ });
  expect(screen.getByRole("option", { name: /Ольга/ })).toBeVisible();
  await user.selectOptions(screen.getByLabelText("Ответственное лицо"), "m");
  await user.type(screen.getByLabelText("Код подтверждения"), "wrong-code");
  await user.click(screen.getByRole("button", { name: "Подтвердить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Неверный код");
  expect(resolve).not.toHaveBeenCalled(); expect(close).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Код подтверждения")).toHaveValue("");
  await user.type(screen.getByLabelText("Код подтверждения"), "right-code");
  await user.click(screen.getByRole("button", { name: "Подтвердить" }));
  await waitFor(() => expect(resolve).toHaveBeenCalledWith({ saved: true }));
  expect(execute).toHaveBeenLastCalledWith({ signer_id: "m", pin: "right-code" });
});

it("onboarding explains blank versus zero and code on save in three short steps", async () => {
  const close = vi.fn(); const user = userEvent.setup();
  render(<Onboarding onClose={close} />);
  await user.click(screen.getByRole("button", { name: "Далее" }));
  expect(screen.getByText(/Ноль означает/)).toHaveTextContent("Пустая ячейка");
  await user.click(screen.getByRole("button", { name: "Далее" }));
  expect(screen.getByText(/личный код руководителя проекта/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Начать заполнение" }));
  expect(close).toHaveBeenCalledTimes(1);
});

it("does not offer database-unlock enrollment for a project manager", async () => {
  const { ResponsibleUsers } = await import("../../src/features/access/ResponsibleUsers");
  const gateway = Object.assign(new DemoGateway(), { listReportSigners: async () => [{ ...admin, can_unlock: true }, { ...manager, can_unlock: false }] });
  render(<ResponsibleUsers gateway={gateway} />);
  expect(await screen.findByText("Через настройку проверяющим или администратором")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Разрешить вход" })).toBeNull();
  await userEvent.setup().selectOptions(screen.getByLabelText("Роль"), "project_manager");
  expect(screen.getByText(/Открывает отчёты автоматически/)).toBeVisible();
});


it("opens report planning without administrator access and keeps users and structure private", async () => {
  const gateway = new DemoGateway();
  const user = userEvent.setup();
  render(<ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider>);
  await screen.findByText("Ежедневное движение и остатки");
  await user.click(screen.getByRole("button", { name: "План и сведения" }));
  expect(screen.getByRole("button", { name: "К заполнению отчётов" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Ответственные лица" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Настроить рабочее поле" })).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(screen.getByRole("button", { name: "К заполнению отчётов" }));
  expect(screen.getByRole("button", { name: "Администратор" })).toBeVisible();
});

it("creates a responsible person inside one administrator session and ends it on exit", async () => {
  localStorage.setItem("reporting-onboarding-v1", "done");
  const authenticate = vi.fn(async () => admin);
  const end = vi.fn(async () => undefined);
  const create = vi.fn(async () => reviewer);
  const gateway = Object.assign(new DemoGateway(), {
    listReportSigners: async () => [admin, manager],
    authenticateAccess: authenticate, endAdministration: end, createReportSigner: create,
  });
  Object.defineProperty(gateway, "mode", { value: "pywebview" });
  const user = userEvent.setup();
  render(<ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider>);
  await screen.findByText("Ежедневное движение и остатки");
  await user.click(screen.getByRole("button", { name: "Администратор" }));
  await screen.findByRole("option", { name: /Анна/ });
  expect(screen.queryByRole("option", { name: /Ольга/ })).toBeNull();
  await user.type(screen.getByLabelText("Код подтверждения"), "secret-code");
  await user.click(screen.getByRole("button", { name: "Подтвердить" }));
  await user.click(await screen.findByRole("button", { name: "Ответственные лица" }));
  await user.type(screen.getByLabelText("Имя ответственного"), "Иван");
  await user.type(screen.getByLabelText("Личный код (от 6 символов)"), "new-code");
  await user.type(screen.getByLabelText("Повтор личного кода"), "new-code");
  await user.click(screen.getByRole("button", { name: "Создать ключ ответственного" }));
  await screen.findByText(/Ключ создан: Иван/);
  expect(create).toHaveBeenCalledWith({ display_name: "Иван", pin: "new-code", role: "reviewer" });
  expect(authenticate).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(screen.getByRole("button", { name: "К заполнению отчётов" }));
  await screen.findByRole("button", { name: "Администратор" });
  expect(end).toHaveBeenCalledTimes(1);
});

it("does not show ordinary entry until the administrator session has ended", async () => {
  const end = vi.fn().mockRejectedValueOnce(new Error("Повторите выход")).mockResolvedValueOnce(undefined);
  const gateway = Object.assign(new DemoGateway(), { endAdministration: end });
  const user = userEvent.setup();
  render(<ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider>);
  await screen.findByText("Ежедневное движение и остатки");
  await user.click(screen.getByRole("button", { name: "Администратор" }));
  await user.click(screen.getByRole("button", { name: "К заполнению отчётов" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Повторите выход");
  expect(screen.getByRole("button", { name: "Ответственные лица" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Администратор" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "К заполнению отчётов" }));
  await screen.findByRole("button", { name: "Администратор" });
  expect(end).toHaveBeenCalledTimes(2);
});

it("cancels confirmation without calling the operation or retaining the entered code", async () => {
  const gateway = Object.assign(new DemoGateway(), { listReportSigners: async () => [manager] });
  const execute = vi.fn(); const reject = vi.fn(); const close = vi.fn();
  render(<AuthorizationDialog gateway={gateway} pending={{ title: "Проверить отчёт перед печатью", adminOnly: false, confirmLabel: "Подтвердить и печатать", execute, resolve: vi.fn(), reject }} onClose={close} />);
  const user = userEvent.setup();
  await screen.findByRole("option", { name: /Ольга/ });
  await user.type(screen.getByLabelText("Код подтверждения"), "secret-code");
  await user.click(screen.getByRole("button", { name: "Отмена" }));
  expect(execute).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Код подтверждения")).toHaveValue("");
  expect(reject).toHaveBeenCalledWith(expect.objectContaining({ message: "Действие отменено. Введённые изменения остались в форме." }));
  expect(close).toHaveBeenCalledTimes(1);
});


it("opens saved reports without asking for a personal code when Windows restores the database key", async () => {
  localStorage.setItem("reporting-onboarding-v1", "done");
  const unlock = vi.fn();
  const gateway = Object.assign(new DemoGateway(), {
    getAccessStatus: async (): Promise<AccessStatus> => ({ state: "ready", users: [admin], current_user: null, automatic_open_available: true }),
    unlockAccess: unlock,
  });
  render(<ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider>);
  await screen.findByText("Ежедневное движение и остатки");
  expect(screen.queryByLabelText("Код доступа")).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(unlock).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Ответственные лица" })).toBeNull();
});

it("explains the one-time setup and shows an automatic-open warning without hiding ready reports", async () => {
  const gateway = Object.assign(new DemoGateway(), {
    getAccessStatus: async (): Promise<AccessStatus> => ({ state: "locked", users: [admin], automatic_open_error: "Не удалось прочитать настройку Windows." }),
    unlockAccess: async (): Promise<AccessStatus> => ({ state: "ready", users: [admin], automatic_open_error: "Не удалось сохранить настройку Windows." }),
  });
  render(<AccessGate gateway={gateway}><p>Рабочая форма</p></AccessGate>);
  expect(await screen.findByText("Настроить открытие без кода")).toBeVisible();
  expect(screen.getByText(/Один раз введите действующий код/)).toBeVisible();
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Код доступа"), "right-code");
  await user.click(screen.getByRole("button", { name: "Открыть отчёты" }));
  expect(await screen.findByText("Рабочая форма")).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent("Не удалось сохранить настройку Windows.");
});
