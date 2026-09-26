import { UiIcon } from "../../shared/ui/UiIcon";
import { useEffect, useState } from "react";
import type { AccessRole, ApplicationGateway, ReportSigner, ManageReportSignerRequest } from "../../shared/api/application-gateway";
import { ROLE_LABELS } from "./AccessGate";

export function ResponsibleUsers({ gateway, onAdministrationChanged }: { gateway: ApplicationGateway; onAdministrationChanged?(message: string): void }) {
  const [users, setUsers] = useState<ReportSigner[]>([]);
  const [name, setName] = useState("");
  const [role, setRole] = useState<AccessRole>("reviewer");
  const [pin, setPin] = useState("");
  const [repeat, setRepeat] = useState("");
  const [enrollId, setEnrollId] = useState("");
  const [enrollPin, setEnrollPin] = useState("");
  const [lifecycle, setLifecycle] = useState<ManageReportSignerRequest | null>(null);
  const [currentPin, setCurrentPin] = useState("");
  const [newPin, setNewPin] = useState("");
  const [repeatPin, setRepeatPin] = useState("");
  function chooseAction(user: ReportSigner, action: ManageReportSignerRequest["action"]) {
    setLifecycle({ action, signer_id: user.id }); setCurrentPin(""); setNewPin(""); setRepeatPin(""); setError(""); setMessage("");
  }
  async function manage() {
    if (!lifecycle || !gateway.manageReportSigner) return;
    if (lifecycle.action === "change_pin" && newPin !== repeatPin) { setError("Новые коды не совпадают."); return; }
    setBusy(true); setError(""); setMessage("");
    const request = { ...lifecycle, ...(lifecycle.action !== "revoke" ? { current_pin: currentPin } : {}), ...(lifecycle.action === "change_pin" ? { new_pin: newPin } : {}) };
    setCurrentPin(""); setNewPin(""); setRepeatPin("");
    try {
      const result = await gateway.manageReportSigner(request);
      setUsers(result.users); setLifecycle(null);
      const message = result.access_warning ?? "Доступ изменён. Прежние подписи отчётов сохранены.";
      setMessage(message);
      if (result.access_warning) window.dispatchEvent(new CustomEvent("backup-warning", { detail: result.access_warning }));
      onAdministrationChanged?.(message + " Для следующего административного действия войдите в раздел заново.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    void gateway.listReportSigners?.().then(setUsers).catch(reason => setError(String(reason)));
  }, [gateway]);
  async function create() {
    if (pin !== repeat) { setError("Коды не совпадают."); return; }
    setBusy(true); setError(""); setMessage("");
    const secret = pin; setPin(""); setRepeat("");
    try {
      const user = await gateway.createReportSigner!({ display_name: name.trim(), role, pin: secret });
      setUsers(current => [...current, user]); setName(""); setMessage(`Ключ создан: ${user.display_name}. Передайте личный код этому пользователю.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  async function enroll() {
    setBusy(true); setError(""); setMessage("");
    const secret = enrollPin; setEnrollPin("");
    try {
      await gateway.enrollAccess!({ signer_id: enrollId, pin: secret });
      setUsers(current => current.map(user => user.id === enrollId ? { ...user, can_unlock: true } : user));
      setEnrollId(""); setMessage("Вход с личным кодом разрешён.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  return <section className="admin-users" aria-labelledby="responsible-title"><h2 id="responsible-title">Ответственные лица</h2>
    <p>Администратор один. Проверяющих и руководителей проекта можно добавить несколько. Обычный запуск не требует кода. Разрешение ниже нужно для открытия базы после переноса на другой компьютер или восстановления.</p>
    <table><thead><tr><th>Имя</th><th>Роль</th><th>Открытие после переноса</th><th>Управление доступом</th></tr></thead><tbody>{users.map(user => <tr key={user.id}><td>{user.display_name}</td><td>{ROLE_LABELS[user.role]}{user.revoked ? " · доступ отозван" : ""}</td><td>{user.revoked ? "Отозван" : user.role === "project_manager" ? "Через настройку проверяющим или администратором" : user.can_unlock === false ? <button type="button" className="button secondary" disabled={busy} onClick={() => { setEnrollId(user.id); setEnrollPin(""); }}><UiIcon name="key" />Разрешить вход</button> : "Разрешён"}</td><td>{!user.revoked && gateway.manageReportSigner && <div className="access-actions"><button type="button" disabled={busy} onClick={() => chooseAction(user, "change_pin")}>Сменить код</button>{user.role !== "admin" && <><button type="button" disabled={busy} onClick={() => chooseAction(user, "revoke")}>Отозвать доступ</button><button type="button" disabled={busy} onClick={() => chooseAction(user, "transfer_admin")}>Назначить администратором</button></>}</div>}</td></tr>)}</tbody></table>
    {lifecycle && <form className="access-card" onSubmit={event => { event.preventDefault(); void manage(); }}>
      <h3>{lifecycle.action === "change_pin" ? "Смена кода" : lifecycle.action === "revoke" ? "Отзыв доступа" : "Передача прав администратора"}: {users.find(user => user.id === lifecycle.signer_id)?.display_name}</h3>
      <p>{lifecycle.action === "revoke" ? "Пользователь больше не сможет сохранять и подписывать отчёты или открывать базу своим кодом. Старые подписи сохранятся. Отозванный профиль нельзя восстановить; при необходимости создайте новый профиль с отличающимся именем." : lifecycle.action === "transfer_admin" ? "Выбранный пользователь станет единственным администратором. Прежний администратор станет проверяющим. После подтверждения раздел администратора закроется." : "Нужен действующий код пользователя. Новый код сохранит прежний ключ и подписи. Забытый код восстановить нельзя. Для обычного пользователя администратор может отозвать доступ и создать новый профиль с отличающимся именем."}</p>
      {lifecycle.action !== "revoke" && <label>Действующий код выбранного пользователя<input type="password" autoComplete="off" maxLength={128} value={currentPin} disabled={busy} onChange={event => setCurrentPin(event.target.value)} /></label>}
      {lifecycle.action === "change_pin" && <><label>Новый код<input type="password" autoComplete="new-password" maxLength={128} value={newPin} disabled={busy} onChange={event => setNewPin(event.target.value)} /></label><label>Повтор нового кода<input type="password" autoComplete="new-password" maxLength={128} value={repeatPin} disabled={busy} onChange={event => setRepeatPin(event.target.value)} /></label></>}
      <div className="access-actions"><button type="button" disabled={busy} onClick={() => { setLifecycle(null); setCurrentPin(""); setNewPin(""); setRepeatPin(""); }}>Отмена</button><button disabled={busy || (lifecycle.action !== "revoke" && currentPin.length < 6) || (lifecycle.action === "change_pin" && (newPin.length < 6 || !repeatPin))}>Продолжить с подтверждением администратора</button></div>
    </form>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {enrollId && <form className="access-card" onSubmit={e => { e.preventDefault(); void enroll(); }}><h3>Разрешить вход: {users.find(user => user.id === enrollId)?.display_name}</h3>
      <p>Для ключа из прежней версии введите его действующий личный код. Затем подтвердите действие кодом администратора.</p>
      <label>Действующий личный код<input type="password" autoComplete="off" maxLength={128} value={enrollPin} disabled={busy} onChange={e => setEnrollPin(e.target.value)} /></label>
      <div className="access-actions"><button type="button" className="button secondary" disabled={busy} onClick={() => { setEnrollId(""); setEnrollPin(""); }}>Отмена</button><button className="button primary" disabled={busy || enrollPin.length < 6}><UiIcon name="key" />Разрешить вход с этим кодом</button></div>
    </form>}
    <form className="access-card" onSubmit={e => { e.preventDefault(); void create(); }}><h3>Добавить ответственное лицо</h3>
      <label>Имя ответственного<input required maxLength={120} disabled={busy} value={name} onChange={e => setName(e.target.value)} /></label>
      <label>Роль<select value={role} disabled={busy} onChange={e => setRole(e.target.value as AccessRole)}><option value="reviewer">Проверяющий</option><option value="project_manager">Руководитель проекта</option></select></label>
      <p>{role === "reviewer" ? "Редактирует планы, сведения и значения отчёта. Сохраняет и подтверждает данные своим кодом." : "Открывает отчёты автоматически на настроенном компьютере. Редактирует планы и сведения, сохраняет и подтверждает данные своим кодом."}</p>
      <label>Личный код (от 6 символов)<input type="password" autoComplete="new-password" minLength={6} maxLength={128} disabled={busy} value={pin} onChange={e => setPin(e.target.value)} /></label>
      <label>Повтор личного кода<input type="password" autoComplete="new-password" minLength={6} maxLength={128} disabled={busy} value={repeat} onChange={e => setRepeat(e.target.value)} /></label>
      <button className="button primary" disabled={busy || !gateway.createReportSigner || !name.trim() || pin.length < 6 || !repeat}><UiIcon name="key" />{busy ? "Создаём…" : "Создать ключ ответственного"}</button>
    </form>
  </section>;
}
