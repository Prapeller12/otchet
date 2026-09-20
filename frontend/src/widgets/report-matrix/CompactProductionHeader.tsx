import { useEffect, useState } from "react";
import { UiIcon } from "../../shared/ui/UiIcon";
import type { ProductionHeaderProps, ReportHeaderFields } from "./ProductionHeader";

/** Only the head-site header changes; hidden metadata is preserved on save. */
export function CompactProductionHeader({ presentation, blocked, onSave, onDirtyChange, onBusyChange }: ProductionHeaderProps) {
  const baseline = JSON.stringify({ product_designation: "", product_name: "", factory_name: "", product_image: "", ...presentation.header });
  const [draft, setDraft] = useState<ReportHeaderFields>(() => JSON.parse(baseline));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dirty = JSON.stringify(draft) !== baseline;
  useEffect(() => { setDraft(JSON.parse(baseline)); }, [baseline]);
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  async function save() {
    if (!dirty || blocked || busy) return;
    setBusy(true); onBusyChange?.(true); setError("");
    try { await onSave({ header: draft }); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); onBusyChange?.(false); }
  }
  function cancel() { setDraft(JSON.parse(baseline)); setError(""); }
  return <section className="compact-production-header" aria-label="Шапка изделия">
    <form onSubmit={event => { event.preventDefault(); void save(); }}>
      <fieldset disabled={blocked || busy} className="compact-production-row">
        <span className="compact-annual-plan">Годовой план: <output aria-label="Годовой план" title="Сумма сохранённых месячных планов">{presentation.annual?.plan || "—"}</output></span>
        <label className="compact-product-name">Название изделия<input maxLength={200} value={draft.product_name} onChange={e => setDraft(current => ({ ...current, product_name: e.target.value }))} /></label>
        <label className="compact-product-code">Шифр изделия<input maxLength={200} value={draft.product_designation} onChange={e => setDraft(current => ({ ...current, product_designation: e.target.value }))} /></label>
        <button type="submit" className="mini-button compact-header-action" disabled={!dirty} aria-label="Сохранить шапку" title="Сохранить шапку"><UiIcon name="save" /></button>
        {dirty && <button type="button" className="mini-button compact-header-action" onClick={cancel} aria-label="Отменить изменения шапки" title="Отменить изменения шапки"><UiIcon name="undo" /></button>}
      </fieldset>
    </form>
    {error && <p role="alert" className="save-error">{error}</p>}
  </section>;
}
