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

it("preserves prior-year code data while editing monthly zero and blank cells", async () => {
  const save = vi.fn(async () => {}); const user = userEvent.setup();
  render(<ProductionHeader presentation={{ production_codes: [{ id: "A", label: "Код A", plans: { "2025-12": "10" }, actuals: {} }] }} year={2026} headSite blocked={false} onSave={save} onDirtyChange={vi.fn()} />);
  await user.type(screen.getByLabelText("Код A: план 2026-01"), "0");
  await user.click(screen.getByRole("button", { name: "Сохранить шапку и выпуск" }));
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ production_codes: [{ id: "A", label: "Код A", plans: { "2025-12": "10", "2026-01": "0" }, actuals: {} }] }));
  expect(screen.getByRole("button", { name: "Убрать код" })).toBeDisabled();
  expect(screen.getByLabelText("Код A: факт 2026-01")).toHaveValue("");
});

it("keeps failed-save draft and requires the user to supply a code label", async () => {
  const save = vi.fn(async () => { throw new Error("Итоги расходятся"); }); const user = userEvent.setup();
  render(<ProductionHeader presentation={{}} year={2026} headSite blocked={false} onSave={save} onDirtyChange={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "+ Код выпуска" }));
  expect(screen.getByRole("button", { name: "Сохранить шапку и выпуск" })).toBeDisabled();
  await user.type(screen.getByLabelText("Код / модификация"), "Исполнение A");
  await user.click(screen.getByRole("button", { name: "Сохранить шапку и выпуск" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Итоги расходятся");
  expect(screen.getByLabelText("Код / модификация")).toHaveValue("Исполнение A");
});
