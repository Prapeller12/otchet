import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { RecoveryBackups } from "../../src/features/access/RecoveryBackups";
import { AccessGate } from "../../src/features/access/AccessGate";
import { DemoGateway } from "../../src/shared/api/demo-gateway";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const backups = [
  { backup_id: "snapshot-good", path: "/backup/good/reporting.sqlite3", valid: true, created_at: "2026-09-26T10:00:00Z" },
  { backup_id: "snapshot-bad", path: "/backup/bad/reporting.sqlite3", valid: false, error: "Повреждён файл" },
];

it("blocks damaged sets and presents the new recovery directory and next steps", async () => {
  const restore = vi.fn(async () => ({ cancelled: false, directory: "C:/Reports/recovery-1", instructions: ["Откройте отдельную копию программы."] }));
  const gateway = Object.assign(new DemoGateway(), {
    listRecoveryBackups: async () => ({ backups }),
    verifyRecoveryBackup: vi.fn(async () => ({ valid: true, created_at: backups[0]!.created_at! })),
    restoreRecoveryBackup: restore,
  });
  const user = userEvent.setup();
  render(<RecoveryBackups gateway={gateway} onClose={vi.fn()} />);
  const select = await screen.findByLabelText("Сохранённый комплект");
  await waitFor(() => expect(select).not.toBeDisabled());
  await user.selectOptions(select, "snapshot-bad");
  expect(screen.getByRole("button", { name: "Восстановить в новую папку" })).toBeDisabled();
  await user.selectOptions(select, "snapshot-good");
  await user.click(screen.getByRole("button", { name: "Восстановить в новую папку" }));
  expect(await screen.findByText("C:/Reports/recovery-1")).toBeVisible();
  expect(screen.getByText("Откройте отдельную копию программы.")).toBeVisible();
  expect(restore).toHaveBeenCalledWith("snapshot-good");
});

it("offers recovery when a damaged live vault prevents the access screen from loading", async () => {
  const gateway = Object.assign(new DemoGateway(), {
    getAccessStatus: vi.fn().mockRejectedValue(new Error("Повреждён файл доступа")),
    listRecoveryBackups: async () => ({ backups }),
  });
  render(<AccessGate gateway={gateway}><p>Отчёты</p></AccessGate>);
  await screen.findByText("Повреждён файл доступа");
  await userEvent.setup().click(screen.getByRole("button", { name: "Восстановить из резервной копии" }));
  expect(await screen.findByRole("dialog", { name: "Резервные копии" })).toBeVisible();
  expect(screen.queryByText("Отчёты")).toBeNull();
});
