import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ProductionHeader } from "../../src/widgets/report-matrix/ProductionHeader";

afterEach(cleanup);

it("edits header only on explicit save, tracks dirty state and cancels", async () => {
  const save = vi.fn(async () => {}); const dirty = vi.fn(); const user = userEvent.setup();
  render(<ProductionHeader presentation={{}} year={2026} headSite={false} blocked={false} onSave={save} onDirtyChange={dirty} />);
  await user.type(screen.getByLabelText("Шифр изделия"), "TEST-A");
  await user.tab();
  expect(save).not.toHaveBeenCalled(); expect(dirty).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByRole("button", { name: "Сохранить шапку и выпуск" }));
  expect(save).toHaveBeenCalledWith({ header: { product_designation: "TEST-A", product_name: "", factory_name: "", product_image: "" } });
  await user.click(screen.getByRole("button", { name: "Отменить изменения шапки" }));
  expect(screen.getByLabelText("Шифр изделия")).toHaveValue("");
  expect(dirty).toHaveBeenLastCalledWith(false);
});

it("shows only annual plan, name and code, preserving hidden metadata and code history on save", async () => {
  const save = vi.fn(async () => {}); const user = userEvent.setup();
  const header = { product_designation: "A", product_name: "Изделие", factory_name: "Завод", product_image: "stored-image" };
  render(<ProductionHeader presentation={{ header, annual: { plan: "0", actual: "10", completion: "", plan_months: 1, actual_months: 1 }, production_codes: [{ id: "A", label: "Код A", plans: { "2025-12": "10" }, actuals: {} }] }} year={2026} headSite blocked={false} onSave={save} onDirtyChange={vi.fn()} />);
  expect(screen.getByLabelText("Годовой план")).toHaveTextContent("0");
  expect(screen.getAllByRole("textbox")).toHaveLength(2);
  expect(screen.queryByLabelText("Завод / изготовитель")).toBeNull();
  expect(screen.queryByText("Код выпуска")).toBeNull();
  expect(screen.queryByText(/Выпущено за год/)).toBeNull();
  await user.type(screen.getByLabelText("Название изделия"), " 2");
  await user.click(screen.getByRole("button", { name: "Сохранить шапку" }));
  expect(save).toHaveBeenCalledWith({ header: { ...header, product_name: "Изделие 2" } });
});

it("retains a compact header draft after a failed save", async () => {
  const save = vi.fn(async () => { throw new Error("Не удалось сохранить"); }); const user = userEvent.setup();
  render(<ProductionHeader presentation={{}} year={2026} headSite blocked={false} onSave={save} onDirtyChange={vi.fn()} />);
  await user.type(screen.getByLabelText("Шифр изделия"), "TEST");
  await user.click(screen.getByRole("button", { name: "Сохранить шапку" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось сохранить");
  expect(screen.getByLabelText("Шифр изделия")).toHaveValue("TEST");
  await user.click(screen.getByRole("button", { name: "Отменить изменения шапки" }));
  expect(screen.getByLabelText("Шифр изделия")).toHaveValue("");
});
