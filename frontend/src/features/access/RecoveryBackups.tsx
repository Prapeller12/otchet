import { useEffect, useState } from "react";
import type { ApplicationGateway, RecoveryBackup, RecoveryResult } from "../../shared/api/application-gateway";
import { useDialogFocus } from "./useDialogFocus";

export function RecoveryBackups({ gateway, onClose }: { gateway: ApplicationGateway; onClose(): void }) {
  const dialogRef = useDialogFocus();
  const [backups, setBackups] = useState<RecoveryBackup[]>([]);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [restored, setRestored] = useState<RecoveryResult | null>(null);
  async function refresh() {
    setBusy(true); setError("");
    try {
      if (!gateway.listRecoveryBackups) throw new Error("Просмотр резервных копий недоступен в этой сборке.");
      const result = await gateway.listRecoveryBackups();
      setBackups(result.backups);
      setSelected(current => result.backups.some(backup => backup.backup_id === current) ? current : result.backups.find(backup => backup.valid)?.backup_id ?? result.backups[0]?.backup_id ?? "");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  useEffect(() => { dialogRef.current?.focus(); void refresh(); }, [gateway]);
  async function verify() {
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await gateway.verifyRecoveryBackup!(selected);
      if (!result.valid) throw new Error("Комплект не прошёл проверку.");
      setBackups(current => current.map(backup => backup.backup_id === selected ? { ...backup, valid: true, error: "" } : backup));
      setMessage("Файлы комплекта соответствуют контрольным суммам. Открытие и чтение отчётов проверьте в восстановленной копии программы.");
    } catch (reason) {
      setBackups(current => current.map(backup => backup.backup_id === selected ? { ...backup, valid: false } : backup));
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setBusy(false); }
  }
  async function restore() {
    setBusy(true); setError(""); setMessage(""); setRestored(null);
    try {
      const result = await gateway.restoreRecoveryBackup!(selected);
      if (result.cancelled) setMessage("Восстановление отменено.");
      else setRestored(result);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  const active = backups.find(backup => backup.backup_id === selected);
  return <div className="excel-dialog-backdrop authorization-backdrop"><section ref={node => { dialogRef.current = node; }} className="access-card" tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="recovery-title">
    <h2 id="recovery-title">Резервные копии</h2>
    <p>Выберите сохранённый комплект. Восстановление создаст отдельную новую папку с данными и ключами доступа. Рабочая база останется на месте.</p>
    <label>Сохранённый комплект<select value={selected} disabled={busy} onChange={event => { setSelected(event.target.value); setError(""); setMessage(""); setRestored(null); }}>
      {!backups.length && <option value="">Копий пока нет</option>}
      {backups.map(backup => <option key={backup.backup_id} value={backup.backup_id}>{backup.created_at ? new Date(backup.created_at).toLocaleString("ru-RU") : backup.backup_id} · {backup.valid ? "файлы проверены" : "ошибка проверки"}</option>)}
    </select></label>
    {active?.error && <p role="alert">{active.error}</p>}
    {active?.application_version && <p>Версия программы: {active.application_version}</p>}
    {!busy && !backups.length && <p>Комплекты появятся после сохранения данных. Копии старого формата в этом списке не отображаются.</p>}
    <p>Для открытия восстановленной базы понадобится код, действовавший на дату копии. В окне выбора укажите папку, внутри которой создать новый комплект.</p>
    {busy && <p role="status">Проверяем и копируем файлы…</p>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {restored && <div role="status"><p>Комплект восстановлен в папку:</p><p style={{ overflowWrap: "anywhere" }}><strong>{restored.directory}</strong></p><ol>{restored.instructions?.map(step => <li key={step}>{step}</li>)}</ol></div>}
    <div className="access-actions"><button type="button" disabled={busy} onClick={() => void refresh()}>Обновить список</button><button type="button" disabled={busy || !selected || !gateway.verifyRecoveryBackup} onClick={() => void verify()}>Проверить</button><button type="button" disabled={busy || !active?.valid || !gateway.restoreRecoveryBackup} onClick={() => void restore()}>Восстановить в новую папку</button><button type="button" disabled={busy} onClick={onClose}>Закрыть</button></div>
  </section></div>;
}
