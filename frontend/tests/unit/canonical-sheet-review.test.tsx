import { useState } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CanonicalSheetReview } from "../../src/features/reference-reports/CanonicalSheetReview";
import { DemoGateway } from "../../src/shared/api/demo-gateway";
import type { ImportPreview } from "../../src/shared/api/application-gateway";
afterEach(cleanup);
it("rechecks the staged canonical file only after an explicit sheet decision and reason", async () => {
  const initial: ImportPreview = { cancelled: false, batch_id: "staged-source", mode: "update", status: "INVALID", error_count: 1, metadata: { sheets: [
    { name: "Отчёт", state: "visible", known: true, requires_decision: false },
    { name: "Архив", state: "hidden", known: false, requires_decision: true, included: null },
  ] } };
  const validateImport = vi.fn(async () => ({ ...initial, status: "STAGED" as const, error_count: 0 }));
  const gateway = Object.assign(new DemoGateway(), { validateImport }); const changed = vi.fn();
  function Preview() { const [preview, setPreview] = useState(initial); return <CanonicalSheetReview preview={preview} gateway={gateway} query={{ report_type: "SUBSIDIARY", organization_id: "1", year: 2026 }} onChange={next => { changed(next); setPreview(next); }} />; }
  render(<Preview />); const user = userEvent.setup();
  expect(screen.queryByLabelText("Действие для дополнительного листа Отчёт")).toBeNull();
  expect(screen.getByRole("button", { name: "Проверить решения по листам" })).toBeDisabled();
  await user.selectOptions(screen.getByLabelText("Действие для дополнительного листа Архив"), "exclude");
  expect(changed).toHaveBeenLastCalledWith(expect.objectContaining({ batch_id: "staged-source", validation_pending: true }));
  expect(screen.getByRole("button", { name: "Проверить решения по листам" })).toBeDisabled();
  await user.type(screen.getByLabelText("Основание решения для листа Архив"), "Архив другого периода");
  await user.click(screen.getByRole("button", { name: "Проверить решения по листам" }));
  expect(validateImport).toHaveBeenCalledWith({ report_type: "SUBSIDIARY", organization_id: "1", year: 2026, mode: "update", batch_id: "staged-source", sheet_decisions: { "Архив": { include: false, reason: "Архив другого периода" } } });
  await waitFor(() => expect(changed).toHaveBeenLastCalledWith(expect.objectContaining({ status: "STAGED", error_count: 0, validation_pending: false })));
});
