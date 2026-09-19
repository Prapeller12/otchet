import { useEffect, useState } from "react";
import type { ApplicationGateway, ReportSigner } from "../../shared/api/application-gateway";

export function ReportSigningDialog({ gateway, busy, error, month, onClose, onSign }: {
  gateway: ApplicationGateway; busy: boolean; error: string; month: string;
  onClose: () => void; onSign: (signerId: string, pin: string) => Promise<void>;
}) {
  const [users, setUsers] = useState<ReportSigner[]>([]);
  const [loading, setLoading] = useState(true);
  const [localError, setLocalError] = useState("");
  const [signerId, setSignerId] = useState("");
  const [pin, setPin] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [newPin, setNewPin] = useState("");
  const [repeatPin, setRepeatPin] = useState("");
  const [adminId, setAdminId] = useState("");
  const [adminPin, setAdminPin] = useState("");
  const disabled = busy || loading;
  const selected = users.find(user => user.id === signerId);
  useEffect(() => {
    let active = true;
    void gateway.listReportSigners!().then(result => {
      if (!active) return;
      setUsers(result); setSignerId(result[0]?.id ?? "");
      setAdminId(result.find(user => user.role === "admin")?.id ?? "");
      setCreating(result.length === 0);
    }).catch(reason => { if (active) setLocalError(String(reason instanceof Error ? reason.message : reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [gateway]);

  async function create() {
    if (newPin !== repeatPin) { setLocalError("PIN и повтор PIN не совпадают."); return; }
    setLoading(true); setLocalError("");
    try {
      const profile = await gateway.createReportSigner!({ display_name: name.trim(), pin: newPin,
        ...(users.length ? { admin_id: adminId, admin_pin: adminPin } : {}) });
      setUsers([...users, profile]); setSignerId(profile.id);
      if (profile.role === "admin") setAdminId(profile.id);
      setCreating(false); setName(""); setConfirmed(false);
    } catch (reason) { setLocalError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setLoading(false); setNewPin(""); setRepeatPin(""); setAdminPin(""); }
  }
  async function sign() {
    const secret = pin;
    setPin(""); setLocalError("");
    await onSign(signerId, secret);
  }
  function cancelCreation() {
    setCreating(false); setNewPin(""); setRepeatPin(""); setAdminPin(""); setLocalError("");
  }
  return <div className="excel-dialog-backdrop"><section className="excel-dialog verification-form" role="dialog" aria-modal="true" aria-labelledby="verification-title">
    <h3 id="verification-title">Подтверждение данных — {month}</h3>
    <p>Подписывается сохранённый отчёт за выбранный месяц вместе со сводными данными с начала года.</p>
    {loading && <p role="status">Загрузка…</p>}
    {creating ? <>
      <h4>{users.length ? "Новый пользователь" : "Создание первого пользователя"}</h4>
      {!users.length && <p>Первый пользователь станет администратором ключей. Его PIN нужен для создания следующих пользователей.</p>}
      <label>ФИО пользователя<input type="text" autoFocus maxLength={120} value={name} disabled={disabled} onChange={e => setName(e.target.value)} /></label>
      <label>Новый PIN (от 6 символов)<input type="password" autoComplete="new-password" maxLength={128} value={newPin} disabled={disabled} onChange={e => setNewPin(e.target.value)} /></label>
      <label>Повтор PIN<input type="password" autoComplete="new-password" maxLength={128} value={repeatPin} disabled={disabled} onChange={e => setRepeatPin(e.target.value)} /></label>
      {users.length > 0 && <>
        <label>Администратор ключей<select value={adminId} disabled={disabled} onChange={e => setAdminId(e.target.value)}>{users.filter(user => user.role === "admin").map(user => <option key={user.id} value={user.id}>{user.display_name}</option>)}</select></label>
        <label>PIN администратора<input type="password" autoComplete="off" maxLength={128} value={adminPin} disabled={disabled} onChange={e => setAdminPin(e.target.value)} /></label>
      </>}
      <p>Сохраните PIN: восстановить его нельзя. Для переноса ключей нужна резервная копия базы программы.</p>
      <button className="button primary" disabled={disabled || !name.trim() || newPin.length < 6 || !repeatPin || (users.length > 0 && !adminPin)} onClick={() => void create()}>Создать ключ пользователя</button>
      {users.length > 0 && <button className="button secondary" disabled={disabled} onClick={cancelCreation}>Вернуться к подписи</button>}
    </> : <>
      <label>Подтверждающий пользователь<select autoFocus value={signerId} disabled={disabled} onChange={e => { setSignerId(e.target.value); setPin(""); setConfirmed(false); }}>
        {users.map(user => <option key={user.id} value={user.id}>{user.display_name} · {user.key_fingerprint}</option>)}
      </select></label>
      {selected && <p>Ключ Ed25519: {selected.key_fingerprint}</p>}
      <label>PIN пользователя<input type="password" autoComplete="off" maxLength={128} value={pin} disabled={disabled} onChange={e => setPin(e.target.value)} /></label>
      <label><input type="checkbox" checked={confirmed} disabled={disabled} onChange={e => setConfirmed(e.target.checked)} /> Я проверил данные и подтверждаю их верность.</label>
      <button className="button secondary" disabled={disabled} onClick={() => { setCreating(true); setPin(""); setConfirmed(false); }}>Добавить пользователя</button>
      <p>Внутренняя криптографическая подпись данных. В PDF печатаются две строки с автором, временем, отпечатком ключа и подписью.</p>
    </>}
    {(localError || error) && <p role="alert" style={{ whiteSpace: "pre-wrap", maxHeight: "25vh", overflowY: "auto" }}>{localError || error}</p>}
    <div className="excel-dialog-actions">
      <button className="button secondary" disabled={disabled} onClick={onClose}>Отмена</button>
      {!creating && <button className="button primary" disabled={disabled || !confirmed || !signerId || pin.length < 6} onClick={() => void sign()}>{busy ? "Подтверждение…" : "Подтверждаю верность данных"}</button>}
    </div>
  </section></div>;
}
