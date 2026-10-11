import { useDialogFocus } from "../../features/access/useDialogFocus";

export function DraftTransitionDialog({ count, busy, error, onSave, onDiscard, onStay }: {
  count: number; busy: boolean; error: string | null;
  onSave(): void; onDiscard(): void; onStay(): void;
}) {
  const ref = useDialogFocus();
  return <div className="excel-dialog-backdrop"><section ref={node => { ref.current = node; }} className="access-card draft-transition" role="dialog" aria-modal="true" aria-labelledby="draft-transition-title" onKeyDown={event => { if (event.key === "Escape" && !busy) onStay(); }}>
    <h2 id="draft-transition-title">Сохранить изменения перед переходом?</h2>
    <p>В черновике изменено полей: {count}. Шапка, выпуск и ячейки сохраняются одним пакетом.</p>
    <p>Черновик находится в открытой программе. До успешной записи он не входит в резервную копию.</p>
    {error && <p role="alert">{error}</p>}
    <div className="access-actions">
      <button autoFocus className="button primary" disabled={busy} onClick={onSave}>Сохранить и перейти</button>
      <button className="button secondary" disabled={busy} onClick={onDiscard}>Отбросить и перейти</button>
      <button className="button secondary" disabled={busy} onClick={onStay}>Остаться</button>
    </div>
  </section></div>;
}
