import { useState } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PositionFieldsEditor } from "../../src/features/workspace-settings/PositionFieldsEditor";
import { WorkspaceSettingsDialog } from "../../src/features/workspace-settings/WorkspaceSettingsDialog";
import type { FieldConfiguration } from "../../src/shared/api/application-gateway";
import { DemoGateway } from "../../src/shared/api/demo-gateway";
import presets from "../../../resources/report-definitions/presets/field-presets.v1.json";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const initial: FieldConfiguration = { category: "PKI", image: "", norm: "", opening: "", indicators: [
  { code: "WRK_DAILY_RECEIVED", label: "Получено", formula: "" },
  { code: "WRK_DAILY_USED", label: "Использовано", formula: "" },
  { code: "WRK_DAILY_BALANCE", label: "Остаток", formula: "" },
] };

describe("configurable position fields", () => {
  it("uploads a picture, applies the source formulas and adds/reorders/removes indicators", async () => {
    const changed = vi.fn();
    const busy = vi.fn();
    function Editor() {
      const [value, setValue] = useState(initial);
      return <PositionFieldsEditor value={value} presets={presets.presets} onBusyChange={busy} onChange={(next) => { setValue(next); changed(next); }} />;
    }
    render(<Editor />);
    const user = userEvent.setup();
    await user.click(screen.getByText(/Изображение, показатели и формулы/));
    await user.upload(screen.getByLabelText("Изображение ПКИ / ДСЕ"), new File(["synthetic bytes"], "position.png", { type: "image/png" }));
    await waitFor(() => expect(screen.getByAltText("Изображение позиции")).toHaveAttribute("src", expect.stringContaining("data:image/png;base64,")));
    expect(busy.mock.calls).toEqual([[true], [false]]);
    await user.click(screen.getByRole("button", { name: "Формулы комплектности из образца" }));
    expect(screen.getAllByLabelText("Формула")[2]).toHaveValue(presets.presets[0]!.indicators[0]!.formula);
    expect(screen.getAllByLabelText("Формула")[3]).toHaveValue(presets.presets[0]!.indicators[1]!.formula);
    await user.type(screen.getByLabelText("Норма входимости"), "3");
    await user.type(screen.getByLabelText("Начальный остаток месяца"), "71");
    await user.click(screen.getByRole("button", { name: "+ Показатель" }));
    expect(screen.getAllByLabelText("Название показателя")).toHaveLength(5);
    await user.click(screen.getAllByRole("button", { name: "Показатель выше" })[4]!);
    expect(screen.getAllByLabelText("Название показателя")[3]).toHaveValue("Новый показатель");
    await user.click(screen.getAllByRole("button", { name: "Убрать показатель" })[3]!);
    await user.click(screen.getByRole("button", { name: "Убрать изображение" }));
    expect(changed.mock.lastCall?.[0]).toMatchObject({ norm: "3", opening: "71", image: "" });
    expect(screen.queryByAltText("Изображение позиции")).toBeNull();
  });

  it("rejects oversized uploads without changing the configuration", async () => {
    const change = vi.fn();
    render(<PositionFieldsEditor value={initial} onChange={change} />);
    const user = userEvent.setup();
    await user.click(screen.getByText(/Изображение, показатели и формулы/));
    await user.upload(screen.getByLabelText("Изображение ПКИ / ДСЕ"), new File([new Uint8Array(2 * 1024 * 1024 + 1)], "large.png", { type: "image/png" }));
    expect(screen.getByRole("alert")).toHaveTextContent("до 2 МБ");
    expect(change).not.toHaveBeenCalled();
  });

  it("saves category/name/formula settings and protects unsaved work on close", async () => {
    const gateway = new DemoGateway();
    const query = { report_type: "DAILY_MOVEMENT" as const, organization_id: "demo-organization" };
    const layout = await gateway.getReportLayout(query);
    layout.rows[0]!.configuration = initial;
    await gateway.saveReportLayout({ ...query, rows: layout.rows });
    const close = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<WorkspaceSettingsDialog gateway={gateway} organizations={(await gateway.listOrganizations()).organizations} initialOrganizationId={query.organization_id} initialReportType={query.report_type} onOrganizationsChange={vi.fn()} onApply={vi.fn()} onClose={close} />);
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Категория позиции"), "DSE");
    await user.clear(screen.getByLabelText("Позиция"));
    await user.type(screen.getByLabelText("Позиция"), "ДСЕ 1");
    await user.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(confirm).toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Копировать позицию" }));
    expect(screen.getAllByLabelText("Категория позиции")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Применить настройки" }));
    await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
    const saved = await gateway.getReportLayout(query);
    expect(saved.rows[0]).toMatchObject({ position_name: "ДСЕ 1", configuration: { category: "DSE" } });
    expect(saved.rows[1]).toMatchObject({ id: null, position_name: "ДСЕ 1 — копия", configuration: { category: "DSE" } });
  });
});
