import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "../../src/app/App";
import { ApplicationGatewayProvider } from "../../src/app/providers/ApplicationGatewayProvider";
import { DemoGateway } from "../../src/shared/api/demo-gateway";

afterEach(cleanup);

describe("workspace settings", () => {
  it("opens a separate settings window and adds report rows", async () => {
    const user = userEvent.setup();
    render(
      <ApplicationGatewayProvider gateway={new DemoGateway()}>
        <App />
      </ApplicationGatewayProvider>,
    );

    expect(await screen.findByText("Ежедневное движение и остатки")).toBeInTheDocument();
    expect(screen.queryByLabelText("Обозначения состояний ячеек")).not.toBeInTheDocument();
    expect(screen.getByText("Демо без записи на диск")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Настроить рабочее поле" }));
    expect(
      await screen.findByRole("dialog", { name: "Организации и строки отчёта" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "+ Добавить строку" }));
    expect(screen.getAllByLabelText("Позиция")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Применить настройки" }));
    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "Организации и строки отчёта" }),
      ).not.toBeInTheDocument();
    });
  });

  it("adds another subsidiary and exposes it in the organization switcher", async () => {
    const user = userEvent.setup();
    render(
      <ApplicationGatewayProvider gateway={new DemoGateway()}>
        <App />
      </ApplicationGatewayProvider>,
    );
    await screen.findByText("Ежедневное движение и остатки");
    await user.click(screen.getByRole("button", { name: "Настроить рабочее поле" }));
    const input = await screen.findByPlaceholderText("Введите наименование");
    await user.type(input, "Дочернее общество 2");
    await user.click(screen.getByRole("button", { name: "Добавить общество" }));

    await waitFor(() => {
      expect(screen.getAllByText("Дочернее общество 2").length).toBeGreaterThan(0);
    });
  });
});
