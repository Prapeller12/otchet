import { useState } from "react";
import { useDialogFocus } from "../access/useDialogFocus";
import { BlankReportsSetup } from "./BlankReportsSetup";
import type { ReportType } from "../../shared/api/report-cell-contract";

export function BlankReportsDialog({ onClose, onCompleted }: {
  onClose(): void;
  onCompleted(result: { organization_id: string; year: number; month: number; report_type: ReportType }): void | Promise<void>;
}) {
  const ref = useDialogFocus();
  const [busy, setBusy] = useState(false);
  return <div className="settings-backdrop"><section ref={node => { ref.current = node; }} className="settings-dialog blank-reports-dialog" role="dialog" aria-modal="true" aria-label="Заготовки отчётов с сентября 2027 года" onKeyDown={event => { if (event.key === "Escape" && !busy) { event.preventDefault(); onClose(); } }}>
    <button autoFocus type="button" className="button secondary" disabled={busy} onClick={onClose}>Закрыть заготовки</button>
    <BlankReportsSetup onBusyChange={setBusy} onCompleted={onCompleted} />
  </section></div>;
}
