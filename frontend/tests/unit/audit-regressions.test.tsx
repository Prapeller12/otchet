import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { ApplicationGateway, ReportMatrixContract, SaveReportPresentationRequest } from "../../src/shared/api/application-gateway";
import type { ReportCellCoordinate } from "../../src/shared/api/report-cell-contract";
import { createDemoMatrix, DemoGateway } from "../../src/shared/api/demo-gateway";
import { ReportMatrix } from "../../src/widgets/report-matrix/ReportMatrix";
import { sourceMatrix } from "../fixtures/source-matrix";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function calendar(): ReportMatrixContract {
  const matrix = createDemoMatrix("DAILY_MOVEMENT");
  matrix.year = 2026;
  matrix.time_columns = ["2026-01-14", "2026-02-01"].map((id) => ({ id, label: id.slice(8), group_label: id.slice(0, 7), width: 76 }));
  matrix.rows = matrix.rows.slice(0, 3).map((row, r) => ({ ...row, cells: row.cells.slice(0, 2).map((cell, c) => ({
    ...cell, column_id: matrix.time_columns[c]!.id,
    value: { kind: "DATA_NOT_PROVIDED" }, state: { access: r === 2 ? "calculated" : "editable", persistence: "saved" },
    coordinate: { ...cell.coordinate, operation_date: matrix.time_columns[c]!.id } as ReportCellCoordinate,
    ...(r === 2 ? { formula: "=BALANCE(RECEIVED,USED)" } : {}),
  })) }));
  return matrix;
}

function show(gateway: ApplicationGateway, initial = calendar()) {
  let latest = initial;
  function Workspace() {
    const [matrix, setMatrix] = useState(initial);
    return <ReportMatrix workspaceMode="admin" gateway={gateway} matrix={matrix} onChange={(next) => { latest = next; setMatrix(next); }} onStatusChange={vi.fn()} />;
  }
  render(<Workspace />);
  return () => latest;
}

import { App } from "../../src/app/App";
import { ApplicationGatewayProvider } from "../../src/app/providers/ApplicationGatewayProvider";
it("retains all unsaved values in recalculation after a rejected save", async()=>{
 const gateway=new DemoGateway(); Object.defineProperty(gateway,"mode",{value:"pywebview"});
 const preview=vi.spyOn(gateway,"getReportMatrix").mockResolvedValue(calendar());
 vi.spyOn(gateway,"saveReportCells").mockRejectedValue(new Error("synthetic disk failure"));
 const latest=show(gateway); const user=userEvent.setup();
 await user.dblClick(screen.getAllByRole("button",{name:/доступна для ввода/})[0]!);
 await user.type(screen.getByRole("textbox"),"20{Enter}");
 await user.click(screen.getByRole("button",{name:"Сохранить (1)"}));
 await screen.findByText("synthetic disk failure");
 expect(latest().rows[0]!.cells[0]!.value).toEqual({kind:"QUANTITY",quantity:"20"});
 expect(latest().rows[0]!.cells[0]!.state.persistence).toBe("error");
 await user.dblClick(screen.getAllByRole("button",{name:/доступна для ввода/})[1]!);
 await user.type(screen.getByRole("textbox"),"3{Enter}");
 await waitFor(()=>expect(preview.mock.lastCall?.[0].preview_changes).toHaveLength(2));
 expect(screen.getByRole("button",{name:"Сохранить (2)"})).toBeEnabled();
});
it("protects active imported Excel cell edits before blur",async()=>{
 const book={id:"sample",file_name:"sample.xlsx",report_type:"DAILY_MOVEMENT",revision:0,warnings:[],errors:[],sheets:[{name:"Sheet",rows:9,columns:1,merges:[],cells:{A9:{value:"375",kind:"n",display:"375"}}}]};
 const gateway=Object.assign(new DemoGateway(),{referenceReport:vi.fn(async q=>q.action==="list"?[{id:"sample",file_name:"sample.xlsx",report_type:"DAILY_MOVEMENT"}]:book)});
 render(<ApplicationGatewayProvider gateway={gateway}><App /></ApplicationGatewayProvider>);
 const user=userEvent.setup();
 await user.selectOptions(await screen.findByLabelText("Сохранённые отчёты Excel"),"sample");
 await user.dblClick(await screen.findByText("375"));
 await user.clear(screen.getByLabelText("Значение A9"));await user.type(screen.getByLabelText("Значение A9"),"400");
 expect(screen.getByRole("button",{name:"Головная площадка"})).toBeDisabled();
 expect(screen.getByRole("button",{name:"Сохранить изменения"})).toBeDisabled();
 const closing=new Event("beforeunload",{cancelable:true});window.dispatchEvent(closing);
 expect(closing.defaultPrevented).toBe(true);
 await user.tab();
 const afterBlur=new Event("beforeunload",{cancelable:true});window.dispatchEvent(afterBlur);
 expect(afterBlur.defaultPrevented).toBe(true);
});