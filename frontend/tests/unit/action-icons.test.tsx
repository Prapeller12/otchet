import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../src/app/App";
import { ApplicationGatewayProvider } from "../../src/app/providers/ApplicationGatewayProvider";
import { createDemoMatrix, DemoGateway } from "../../src/shared/api/demo-gateway";
import { ReportMatrix } from "../../src/widgets/report-matrix/ReportMatrix";
import type { ReportMatrixContract } from "../../src/shared/api/application-gateway";

afterEach(cleanup);

function expectIcon(button: HTMLElement, name: string) {
  const icon = button.querySelector(`svg[data-icon="${name}"]`);
  expect(icon).not.toBeNull();
  expect(icon).toHaveAttribute("aria-hidden", "true");
  expect(icon).toHaveAttribute("focusable", "false");
  expect(icon).toHaveAttribute("stroke", "currentColor");
  expect(icon?.querySelector("path")?.getAttribute("d")).toBeTruthy();
  expect(button).toHaveAccessibleName();
}

it.each<ReportMatrixContract["report_type"]>(["DAILY_MOVEMENT", "HEAD_SITE", "SUBSIDIARY"])("keeps %s actions identifiable by text, icons, and keyboard", async reportType => {
  const user = userEvent.setup();
  const pdf = vi.fn(async () => ({ cancelled: true }));
  const matrix = createDemoMatrix(reportType);
  const gateway = Object.assign(new DemoGateway(), { exportPdf: pdf });
  render(<ReportMatrix workspaceMode="entry" gateway={gateway} matrix={matrix} onChange={vi.fn()} onStatusChange={vi.fn()} />);
  expectIcon(screen.getByRole("button", { name: "Сохранить" }), "save");
  const print = screen.getByRole("button", { name: "Печать / PDF А4" });
  expectIcon(print, "print");
  expect(screen.queryByRole("button", { name: "Подтвердить данные" })).toBeNull();
  const more = screen.getByRole("button", { name: "Ещё" });
  expectIcon(more, "chevron-down");
  more.focus();
  await user.keyboard("{Enter}");
  expect(more).toHaveAttribute("aria-expanded", "true");
  expectIcon(more, "chevron-up");
  expectIcon(screen.getByRole("button", { name: "Импорт Excel" }), "import");
  expectIcon(screen.getByRole("button", { name: "Экспорт Excel" }), "export");
  await user.keyboard(" ");
  expect(more).toHaveAttribute("aria-expanded", "false");
  expectIcon(more, "chevron-down");
  expect(screen.queryByRole("button", { name: "Импорт Excel" })).toBeNull();
  // Action decoration must not turn data cells into noisy icon buttons.
  const cells = document.querySelectorAll("button.matrix-cell-button");
  expect(cells.length).toBeGreaterThan(0);
  cells.forEach(cell => expect(cell.querySelector("svg")).toBeNull());
  await user.click(print);
  await waitFor(() => expect(pdf).toHaveBeenCalledTimes(1));
  expect(print).toHaveAccessibleName("Печать / PDF А4");
});

it("exposes settings add, edit, copy, move, delete and close icons without replacing names", async () => {
  const user = userEvent.setup();
  render(<ApplicationGatewayProvider gateway={new DemoGateway()}><App /></ApplicationGatewayProvider>);
  await screen.findByText("Ежедневное движение и остатки");
  expectIcon(screen.getByRole("button", { name: "Как заполнить" }), "help");
  expectIcon(screen.getByRole("button", { name: "План и сведения" }), "edit");
  const admin = screen.getByRole("button", { name: "Администратор" });
  expectIcon(admin, "key");
  await user.click(admin);
  expectIcon(screen.getByRole("button", { name: "Ответственные лица" }), "users");
  const settings = screen.getByRole("button", { name: "Настроить рабочее поле" });
  expectIcon(settings, "settings");
  await user.click(settings);
  const dialog = within(await screen.findByRole("dialog", { name: "Организации и строки отчёта" }));
  expectIcon(dialog.getByRole("button", { name: "Переименовать" }), "edit");
  expectIcon(dialog.getByRole("button", { name: "Добавить общество" }), "add");
  const add = dialog.getByRole("button", { name: "Добавить строку" });
  expectIcon(add, "add");
  await user.click(add);
  expect(dialog.getAllByLabelText("Позиция")).toHaveLength(2);
  for (const [name, glyph] of [["Копировать позицию", "copy"], ["Переместить выше", "arrow-up"], ["Переместить ниже", "arrow-down"], ["Убрать строку", "trash"]] as const) {
    dialog.getAllByRole("button", { name }).forEach(button => expectIcon(button, glyph));
  }
  expectIcon(dialog.getByRole("button", { name: "Применить настройки" }), "save");
  expectIcon(dialog.getByRole("button", { name: "Закрыть" }), "close");
  expect(dialog.getByRole("button", { name: "Отмена" }).querySelector("svg")).toBeNull();
});
