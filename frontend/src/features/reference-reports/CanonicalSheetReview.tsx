import { useState } from "react";
import type { ApplicationGateway, ImportPreview, ImportRequest } from "../../shared/api/application-gateway";

/** Canonical source is already staged. Recheck that immutable source without another picker. */
export function CanonicalSheetReview({ preview, gateway, query, onChange }: {
  preview: ImportPreview; gateway: ApplicationGateway; query: ImportRequest; onChange(preview: ImportPreview): void;
}) {
  const sheets = (preview.metadata?.sheets ?? preview.metadata?.review_sheets ?? []).filter(sheet => sheet.known !== true || sheet.requires_decision);
  const [decisions, setDecisions] = useState<Record<string, { include: boolean; reason: string }>>(() => Object.fromEntries(
    sheets.filter(sheet => typeof sheet.included === "boolean").map(sheet => [sheet.name, { include: sheet.included!, reason: sheet.reason ?? "" }]),
  ));
  const [busy, setBusy] = useState(false);
  if (!sheets.length) return null;
  const complete = sheets.every(sheet => decisions[sheet.name]?.reason.trim());
  function update(name: string, include: boolean, reason: string) {
    setDecisions(current => ({ ...current, [name]: { include, reason } }));
    // Keep the source batch id: it identifies the staged file for the next validation.
    onChange({ ...preview, validation_pending: true });
  }
  async function check() {
    if (!preview.batch_id || !complete) return;
    setBusy(true);
    onChange({ ...preview, validation_pending: true });
    try {
      const result = await gateway.validateImport({ ...query, mode: preview.mode ?? "update", batch_id: preview.batch_id, sheet_decisions: decisions });
      onChange({ ...result, mode: preview.mode ?? "update", validation_pending: false });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      onChange({ ...preview, validation_pending: false, status: "INVALID", error_count: 1, issues: [{ source_cell: null, code: "VALIDATION_FAILED", message }] });
    } finally { setBusy(false); }
  }
  return <fieldset className="transfer-sheet-decisions" aria-busy={busy}><legend>Дополнительные листы собственной книги</legend>
    <p>Эти листы отсутствуют в карте обмена. Исключение требует причины. Если лист содержит рабочие данные, включите его в проверку: программа сообщит, поддерживается ли его перенос.</p>
    {sheets.map(sheet => {
      const decision = decisions[sheet.name];
      return <div key={sheet.name} className="transfer-period-block"><strong>{sheet.name} · {sheet.state === "visible" ? "видимый лист" : "скрытый лист"}</strong>
        <label>Действие<select aria-label={`Действие для дополнительного листа ${sheet.name}`} disabled={busy} value={decision ? decision.include ? "include" : "exclude" : ""} onChange={event => { if (event.target.value) update(sheet.name, event.target.value === "include", decision?.reason ?? ""); }}>
          <option value="">Выберите действие</option><option value="exclude">Не переносить</option><option value="include">Включить в проверку</option>
        </select></label>
        {decision && <label>{decision.include ? "Основание включения" : "Причина исключения"}<input aria-label={`Основание решения для листа ${sheet.name}`} disabled={busy} value={decision.reason} onChange={event => update(sheet.name, decision.include, event.target.value)} /></label>}
      </div>;
    })}
    <button type="button" disabled={busy || !complete || !preview.batch_id} onClick={() => void check()}>{busy ? "Проверка…" : "Проверить решения по листам"}</button>
  </fieldset>;
}
