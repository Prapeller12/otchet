import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type {
  CommitImportRequest,
  CommitImportResult,
  ExportRequest,
  ExportResult,
  ImportPreview,
  ImportRequest,
} from "../../src/shared/api/application-gateway";
import { createDemoMatrix, DemoGateway } from "../../src/shared/api/demo-gateway";
import { ReportMatrix } from "../../src/widgets/report-matrix/ReportMatrix";

class ExcelTestGateway extends DemoGateway {
  imported = false;
  exported = false;

  override async validateImport(_request: ImportRequest): Promise<ImportPreview> {
    return {
      cancelled: false,
      batch_id: "12345678901234567890123456789012",
      file_name: "daily.xlsx",
      status: "STAGED",
      new_count: 2,
      changed_count: 1,
      same_count: 3,
      error_count: 0,
      issues: [],
    };
  }

  override async commitImport(_request: CommitImportRequest): Promise<CommitImportResult> {
    this.imported = true;
    return {
      batch_id: "12345678901234567890123456789012",
      status: "COMMITTED",
      imported_count: 3,
      same_count: 3,
      backup_file: "backup.sqlite3",
      already_committed: false,
    };
  }

  override async exportReport(_request: ExportRequest): Promise<ExportResult> {
    this.exported = true;
    return {
      cancelled: false,
      file_name: "daily.xlsx",
      exported_cell_count: 20,
    };
  }
}

describe("Excel actions", () => {
  it("previews and confirms import, then performs export", async () => {
    const gateway = new ExcelTestGateway();
    const matrix = createDemoMatrix("DAILY_MOVEMENT");
    matrix.capabilities.import = { enabled: true };
    matrix.capabilities.export = { enabled: true };
    const user = userEvent.setup();

    render(
      <ReportMatrix
        gateway={gateway}
        matrix={matrix}
        onChange={vi.fn()}
        onStatusChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Импорт Excel" }));
    const dialog = await screen.findByRole("dialog", { name: "Проверка импорта Excel" });
    expect(dialog).toBeVisible();
    expect(screen.getByText("daily.xlsx")).toBeVisible();
    expect(within(dialog).getByText("2")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Подтвердить импорт" }));
    await waitFor(() => expect(gateway.imported).toBe(true));
    expect(await screen.findByText(/Импорт завершён: записано 3/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Экспорт Excel" }));
    await waitFor(() => expect(gateway.exported).toBe(true));
    expect(await screen.findByText(/Excel сохранён: daily.xlsx/)).toBeVisible();
  });
});
