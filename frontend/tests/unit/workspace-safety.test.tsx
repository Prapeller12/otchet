import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../src/app/App";
import { ApplicationGatewayProvider } from "../../src/app/providers/ApplicationGatewayProvider";
import { DemoGateway, createDemoMatrix } from "../../src/shared/api/demo-gateway";
import { ReportMatrix } from "../../src/widgets/report-matrix/ReportMatrix";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("protects unfinished and unsaved matrix edits from report, organization and settings navigation", async () => {
  const gateway = new DemoGateway();
  render(<ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider>);
  const user = userEvent.setup();
  const first = (await screen.findAllByRole("button", { name: /доступна для ввода/ }))[0]!;
  await user.dblClick(first);
  const targetTab = screen.getByRole("button", { name: "Головная площадка" });
  expect(targetTab).toBeDisabled();
  expect(screen.getByLabelText("Организация")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Настроить рабочее поле" })).toBeDisabled();
  const input = screen.getByRole("textbox");
  await user.clear(input);
  await user.type(input, "123{Enter}");
  expect(targetTab).toBeDisabled();
  const before = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(before);
  expect(before.defaultPrevented).toBe(true);
  await user.click(screen.getByRole("button", { name: "Сохранить (1)" }));
  await waitFor(() => expect(targetTab).toBeEnabled());
  expect(screen.getByLabelText("Организация")).toBeEnabled();
  expect(screen.getByRole("button", { name: "Настроить рабочее поле" })).toBeEnabled();
  const after = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(after);
  expect(after.defaultPrevented).toBe(false);
});

function matrix() {
  const gateway = new DemoGateway();
  const save = vi.spyOn(gateway, "saveReportCells");
  let latest = createDemoMatrix("DAILY_MOVEMENT");
  function Workspace() {
    const [value, setValue] = useState(latest);
    return <ReportMatrix gateway={gateway} matrix={value} onChange={next => { latest = next; setValue(next); }} onStatusChange={() => {}} />;
  }
  render(<Workspace />);
  return { latest: () => latest, save };
}

function paste(text: string) {
  fireEvent.paste(screen.getAllByRole("button", { name: /доступна для ввода/ })[0]!, { clipboardData: { getData: () => text } });
}

it("previews rectangular Excel paste, retains blank versus zero, and writes only after Save", async () => {
  const { latest, save } = matrix();
  const user = userEvent.setup();
  paste("1,25\t0\n\t8\n");
  expect(screen.getByRole("dialog", { name: "Проверка вставки из Excel" })).toBeVisible();
  expect(latest().rows[0]!.cells[0]!.value).toEqual({ kind: "QUANTITY", quantity: "12" });
  await user.click(screen.getByRole("button", { name: "Вставить проверенный диапазон" }));
  expect(latest().rows[0]!.cells[0]!.value).toEqual({ kind: "QUANTITY", quantity: "1.25" });
  expect(latest().rows[0]!.cells[1]!.value).toEqual({ kind: "QUANTITY", quantity: "0" });
  expect(latest().rows[1]!.cells[0]!.value).toEqual({ kind: "DATA_NOT_PROVIDED" });
  expect(latest().rows[1]!.cells[1]!.value).toEqual({ kind: "QUANTITY", quantity: "8" });
  expect(save).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Сохранить (4)" }));
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ changes: expect.arrayContaining([{ coordinate: latest().rows[1]!.cells[0]!.coordinate, value: { kind: "DATA_NOT_PROVIDED" } }]) }));
});

it.each([
  ["1\t2\n3\t4\n5\t6", "расчётную"],
  ["1\t2\t3\t4\t5\t6", "за границы"],
  ["1\t2\n3", "прямоугольный"],
  ["1\t=SUM(A1)", "Введите число"],
])("rejects invalid paste atomically: %s", (text, message) => {
  const { latest } = matrix();
  const before = JSON.stringify(latest());
  paste(text);
  expect(screen.getByRole("alert")).toHaveTextContent(message);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(JSON.stringify(latest())).toBe(before);
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
});

it("cancels paste without changing values", async () => {
  const { latest } = matrix();
  const before = JSON.stringify(latest());
  paste("7\t8");
  await userEvent.setup().click(screen.getByRole("button", { name: "Отмена вставки" }));
  expect(JSON.stringify(latest())).toBe(before);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("keeps an invalid edited value visible and prevents saving an older value", async () => {
  const { latest, save } = matrix();
  const user = userEvent.setup();
  await user.dblClick(screen.getAllByRole("button", { name: /доступна для ввода/ })[0]!);
  await user.clear(screen.getByRole("textbox"));
  await user.type(screen.getByRole("textbox"), "bad{Enter}");
  expect(screen.getByRole("textbox")).toHaveValue("bad");
  expect(screen.getByRole("alert")).toHaveTextContent("Введите число");
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  expect(latest().rows[0]!.cells[0]!.value).toEqual({ kind: "QUANTITY", quantity: "12" });
  expect(save).not.toHaveBeenCalled();
  await user.clear(screen.getByRole("textbox"));
  await user.type(screen.getByRole("textbox"), "77{Enter}");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole("button", { name: "Сохранить (1)" })).toBeEnabled();
});


it("commits a corrected invalid draft when focus leaves the editor", async () => {
  const { latest } = matrix();
  const user = userEvent.setup();
  await user.dblClick(screen.getAllByRole("button", { name: /доступна для ввода/ })[0]!);
  await user.clear(screen.getByRole("textbox"));
  await user.type(screen.getByRole("textbox"), "bad{Enter}");
  await user.clear(screen.getByRole("textbox"));
  await user.type(screen.getByRole("textbox"), "44");
  fireEvent.blur(screen.getByRole("textbox"));
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(latest().rows[0]!.cells[0]!.value).toEqual({ kind: "QUANTITY", quantity: "44" });
  expect(screen.getByRole("button", { name: "Сохранить (1)" })).toBeEnabled();
});

it("loads the selected historical year and blocks changing it while a matrix draft is unsaved", async () => {
  const gateway = new DemoGateway();
  const load = vi.spyOn(gateway, "getReportMatrix");
  render(<ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider>);
  const user = userEvent.setup();
  await screen.findByRole("heading", { name: "Ежедневное движение и остатки" });
  const year = screen.getByLabelText("Год отчёта");
  expect(year).toHaveValue(String(new Date().getFullYear()));
  expect(year.querySelector('option[value="1900"]')).not.toBeNull();
  expect(year.querySelector('option[value="2100"]')).not.toBeNull();
  expect(year.querySelector('option[value="2101"]')).toBeNull();
  await user.selectOptions(year, "2024");
  await waitFor(() => expect(load).toHaveBeenLastCalledWith(expect.objectContaining({ year: 2024 })));
  const first = (await screen.findAllByRole("button", { name: /доступна для ввода/ }))[0]!;
  await user.dblClick(first);
  expect(year).toBeDisabled();
  await user.clear(screen.getByRole("textbox"));
  await user.type(screen.getByRole("textbox"), "100{Enter}");
  expect(year).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Сохранить (1)" }));
  await waitFor(() => expect(year).toBeEnabled());
  expect(year).toHaveValue("2024");
});
