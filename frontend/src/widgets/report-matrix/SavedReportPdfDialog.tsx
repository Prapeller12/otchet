import { useDialogFocus } from "../../features/access/useDialogFocus";

export function SavedReportPdfDialog({ title, period, week, busy, onConfirm, onClose }: {
  title: string; period: string; week?: string; busy: boolean; onConfirm(): void; onClose(): void;
}) {
  const ref = useDialogFocus();
  return <div className="excel-dialog-backdrop"><section ref={node => { ref.current = node; }} className="access-card" role="dialog" aria-modal="true" aria-labelledby="pdf-options-title" onKeyDown={event => { if (event.key === "Escape" && !busy) { event.preventDefault(); onClose(); } }}>
    <h2 id="pdf-options-title">Печать сохранённого отчёта</h2><p>{title} · {period}</p>
    <p>PDF А4. Будут использованы сохранённые данные выбранного периода. Перед созданием файла потребуется личный код ответственного лица.</p>
    {week && <p>Остаток на конец недели: {week}</p>}
    <div className="access-actions"><button autoFocus type="button" className="button primary" disabled={busy} onClick={onConfirm}>Подтвердить и создать PDF</button><button type="button" className="button secondary" disabled={busy} onClick={onClose}>Отмена</button></div>
  </section></div>;
}
