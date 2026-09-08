import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ReferenceReport } from "../../src/features/reference-reports/ReferenceReport";
import { DemoGateway } from "../../src/shared/api/demo-gateway";
import type { ReferenceWorkbook } from "../../src/shared/api/application-gateway";

it("opens imported cells, saves input and uses backend recalculation", async () => {
  const book: ReferenceWorkbook = { id: "sample", file_name: "Готовый отчёт.xlsx", report_type: "SUBSIDIARY", revision: 0, warnings: [], errors: [],
    sheets: [{ name: "Лист", rows: 9, columns: 12, merges: [], cells: { L9: { value: "375", kind: "n", display: "375" }, I9: { value: "=SUM(L9:L9)", kind: "f", display: "375" } } }] };
  const request = vi.fn().mockResolvedValueOnce(book).mockImplementation(async (q) => {
    if (q.action === "save") return { ...book, revision: 1, sheets: [{ ...book.sheets[0], cells: { L9: { value: "400", kind: "n", display: "400" }, I9: { value: "=SUM(L9:L9)", kind: "f", display: "400" } } }] };
    return { cancelled: true };
  });
  const gateway = Object.assign(new DemoGateway(), { referenceReport: request });
  render(<ReferenceReport gateway={gateway} organizationId="1" identity="sample" onBack={() => {}} onDirty={() => {}} />);
  const values = await screen.findAllByText("375");
  fireEvent.doubleClick(values.find(e => e.classList.contains("reference-editable"))!);
  const input = screen.getByLabelText("Значение L9");
  fireEvent.change(input, { target: { value: "400" } }); fireEvent.blur(input);
  expect(screen.getByRole("button", { name: "Экспорт Excel" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
  await waitFor(() => expect(screen.getAllByText("400")).toHaveLength(2));
  expect(request).toHaveBeenCalledWith({ action: "save", organization_id: "1", id: "sample", revision: 0, changes: [{ sheet: 0, address: "L9", value: "400" }] });
  expect(screen.getByRole("button", { name: "Экспорт Excel" })).toBeEnabled();
});
