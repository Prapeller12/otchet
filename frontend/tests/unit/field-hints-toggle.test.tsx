import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FieldHintsEnabledContext, HintValue } from "../../src/shared/ui/FieldHint";
import { App } from "../../src/app/App";
import { ApplicationGatewayProvider } from "../../src/app/providers/ApplicationGatewayProvider";
import { DemoGateway } from "../../src/shared/api/demo-gateway";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const show = (gateway: DemoGateway) => render(<ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider>);

it("closes an open hint and removes its description/help-only tab stop when disabled, then restores hover and focus", () => {
  const view = render(<FieldHintsEnabledContext.Provider value={true}><HintValue hint="Проверьте исходные данные">Итог</HintValue></FieldHintsEnabledContext.Provider>);
  const field = screen.getByText("Итог");
  fireEvent.focus(field);
  expect(screen.getByRole("tooltip")).toBeVisible();
  view.rerender(<FieldHintsEnabledContext.Provider value={false}><HintValue hint="Проверьте исходные данные">Итог</HintValue></FieldHintsEnabledContext.Provider>);
  expect(screen.queryByRole("tooltip")).toBeNull();
  expect(field).not.toHaveAttribute("aria-describedby");
  expect(field).not.toHaveAttribute("tabindex");
  fireEvent.mouseEnter(field); fireEvent.focus(field);
  expect(screen.queryByRole("tooltip")).toBeNull();
  view.rerender(<FieldHintsEnabledContext.Provider value={true}><HintValue hint="Проверьте исходные данные">Итог</HintValue></FieldHintsEnabledContext.Provider>);
  fireEvent.mouseEnter(field);
  expect(screen.getByRole("tooltip")).toBeVisible();
  fireEvent.keyDown(field, { key: "Escape" });
  fireEvent.focus(field);
  expect(screen.getByRole("tooltip")).toBeVisible();
});

it("applies one persisted toggle to all three report tabs and restores it on a new workspace", async () => {
  const gateway = new DemoGateway();
  const save = vi.spyOn(gateway, "saveUiPreferences");
  const view = show(gateway);
  const checkbox = await screen.findByRole("checkbox", { name: "Подсказки при наведении" });
  await waitFor(() => expect(checkbox).toBeEnabled());
  expect(checkbox).toBeChecked();
  fireEvent.click(checkbox);
  await waitFor(() => expect(save).toHaveBeenCalledWith({ field_hints_enabled: false }));
  for (const label of ["Ежедневный отчёт", "Головная площадка", "Дочерние общества"]) {
    fireEvent.click(screen.getByRole("button", { name: label }));
    await waitFor(() => expect(document.querySelector('.report-matrix [data-field-hint]')).not.toBeNull());
    const field = document.querySelector<HTMLElement>('.report-matrix [data-field-hint]')!;
    fireEvent.mouseEnter(field); fireEvent.focus(field);
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(checkbox).not.toBeChecked();
  }
  view.unmount();
  show(gateway);
  const restored = await screen.findByRole("checkbox", { name: "Подсказки при наведении" });
  await waitFor(() => expect(restored).toBeEnabled());
  expect(restored).not.toBeChecked();
  fireEvent.click(restored);
  await waitFor(() => expect(restored).toBeEnabled());
  const field = document.querySelector<HTMLElement>('.report-matrix [data-field-hint]')!;
  fireEvent.mouseEnter(field);
  expect(screen.getByRole("tooltip")).toBeVisible();
});

it("keeps an unsaved cell and its edit guard when changing hint visibility", async () => {
  const gateway = new DemoGateway();
  const write = vi.spyOn(gateway, "saveReportCells");
  show(gateway);
  const checkbox = await screen.findByRole("checkbox", { name: "Подсказки при наведении" });
  await waitFor(() => expect(checkbox).toBeEnabled());
  const cell = document.querySelector<HTMLElement>('.report-matrix button[aria-readonly="false"]')!;
  fireEvent.doubleClick(cell);
  const input = screen.getByRole("textbox", { name: /Редактирование/ });
  fireEvent.change(input, { target: { value: "123" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await screen.findByRole("button", { name: "Сохранить (1)" });
  const editedCell = document.querySelector<HTMLElement>('.report-matrix button[aria-readonly="false"]')!;
  expect(editedCell).toHaveTextContent("123");
  fireEvent.click(checkbox);
  await waitFor(() => expect(checkbox).toBeEnabled());
  expect(editedCell).toHaveTextContent("123");
  expect(editedCell.isConnected).toBe(true);
  expect(screen.getByRole("button", { name: "Сохранить (1)" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Головная площадка" })).toBeDisabled();
  expect(write).not.toHaveBeenCalled();
});

it("rolls back the toggle and explains a persistence failure", async () => {
  const gateway = new DemoGateway();
  vi.spyOn(gateway, "saveUiPreferences").mockRejectedValue(new Error("disk full"));
  show(gateway);
  const checkbox = await screen.findByRole("checkbox", { name: "Подсказки при наведении" });
  await waitFor(() => expect(checkbox).toBeEnabled());
  fireEvent.click(checkbox);
  expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось сохранить настройку подсказок");
  expect(checkbox).toBeChecked();
  expect(checkbox).toBeEnabled();
});
