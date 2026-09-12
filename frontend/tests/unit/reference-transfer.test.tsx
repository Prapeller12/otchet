import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ReferenceTransfer } from "../../src/features/reference-reports/ReferenceTransfer";
import { createDemoMatrix, DemoGateway } from "../../src/shared/api/demo-gateway";
import type { ReferenceWorkbook } from "../../src/shared/api/application-gateway";

afterEach(cleanup);
it("shows correction hints and stages an explicitly confirmed workspace target", async () => {
  const matrix = createDemoMatrix("HEAD_SITE");
  const book: ReferenceWorkbook = { id: "book", file_name: "head.xlsx", report_type: "HEAD_SITE", revision: 0, warnings: [], errors: [], sheets: [
    { name: "Лист", rows: 9, columns: 12, merges: [], cells: { L9: { kind: "n", value: "375", display: "375" } } },
  ] };
  const referenceReport = vi.fn().mockResolvedValue({ batch_id: "checked", error_count: 0 });
  const gateway = Object.assign(new DemoGateway(), { referenceReport });
  const onReady = vi.fn();
  render(<ReferenceTransfer book={book} matrix={matrix} gateway={gateway} onReady={onReady} />);
  const user = userEvent.setup();
  expect(screen.getByLabelText("Показатель 0:L9")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByLabelText("Показатель 0:L9")).toHaveAttribute("title", expect.stringContaining("Выберите рабочий показатель"));
  const row = matrix.rows.find(r => r.cells[0]?.state.access === "editable")!;
  await user.selectOptions(screen.getByLabelText("Показатель 0:L9"), row.id);
  await user.selectOptions(screen.getByLabelText("Период 0:L9"), row.cells[0]!.column_id);
  await user.clear(screen.getByLabelText("Значение 0:L9"));
  await user.type(screen.getByLabelText("Значение 0:L9"), "0");
  await user.click(screen.getByLabelText("Значение и период верны"));
  await user.click(screen.getByRole("button", { name: "Проверить сопоставление" }));
  expect(referenceReport).toHaveBeenCalledWith(expect.objectContaining({ mappings: [
    { source: "0:L9", coordinate: row.cells[0]!.coordinate, quantity: "0", confirmed: true },
  ] }));
  expect(onReady).toHaveBeenCalledWith({ batch_id: "checked", error_count: 0 });
});
