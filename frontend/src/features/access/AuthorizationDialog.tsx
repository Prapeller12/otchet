import { useEffect, useState } from "react";
import type { AccessUser, ApplicationGateway, AuthorizationPrompt, WriteAuthorization } from "../../shared/api/application-gateway";
import { useDialogFocus } from "./useDialogFocus";
import { ROLE_LABELS } from "./AccessGate";

export type PendingAuthorization = AuthorizationPrompt & {
  execute: (authorization: WriteAuthorization) => Promise<unknown>;
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
};
export function AuthorizationDialog({ gateway, pending, onClose }: { gateway: ApplicationGateway; pending: PendingAuthorization; onClose(): void }) {
  const dialogRef = useDialogFocus();
  const [users, setUsers] = useState<AccessUser[]>([]);
  const [identity, setIdentity] = useState("");
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void gateway.listReportSigners!().then(result => {
      if (!active) return;
      const allowed = result.filter(user => pending.adminOnly ? user.role === "admin" : (user.role === "admin" || user.role === "reviewer"));
      setUsers(allowed); setIdentity(allowed[0]?.id ?? "");
      if (!allowed.length) setError("Нет ответственного лица с правом сохранения. Обратитесь к администратору.");
    }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { active = false; };
  }, [gateway, pending.adminOnly]);
  async function submit() {
    const secret = pin; setPin(""); setBusy(true); setError("");
    try { const result = await pending.execute({ signer_id: identity, pin: secret }); pending.resolve(result); onClose(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  function cancel() { pending.reject(new Error("Сохранение отменено. Изменения остались в форме.")); onClose(); }
  return <div className="excel-dialog-backdrop authorization-backdrop"><form ref={node => { dialogRef.current = node; }} className="access-card authorization-dialog" role="dialog" aria-modal="true" aria-labelledby="authorization-title" onSubmit={e => { e.preventDefault(); void submit(); }}>
    <h2 id="authorization-title">{pending.title}</h2>
    <p>{pending.adminOnly ? "Введите код администратора для этого действия." : "Попросите проверяющего или администратора проверить изменения и ввести свой код."}</p>
    <label>Ответственное лицо<select value={identity} disabled={busy} onChange={e => { setIdentity(e.target.value); setPin(""); }}>{users.map(user => <option value={user.id} key={user.id}>{user.display_name} · {ROLE_LABELS[user.role]}</option>)}</select></label>
    <label>Код подтверждения<input autoFocus type="password" autoComplete="off" maxLength={128} value={pin} disabled={busy} onChange={e => setPin(e.target.value)} /></label>
    {error && <p role="alert">{error}</p>}
    <div className="access-actions"><button className="button secondary" type="button" disabled={busy} onClick={cancel}>Вернуться без сохранения</button><button className="button primary" disabled={busy || !identity || pin.length < 6}>{busy ? "Проверяем…" : "Подтвердить"}</button></div>
  </form></div>;
}
