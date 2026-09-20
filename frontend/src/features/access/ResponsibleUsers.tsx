import { useEffect, useState } from "react";
import type { AccessRole, ApplicationGateway, ReportSigner } from "../../shared/api/application-gateway";
import { ROLE_LABELS } from "./AccessGate";

export function ResponsibleUsers({ gateway }: { gateway: ApplicationGateway }) {
  const [users, setUsers] = useState<ReportSigner[]>([]);
  const [name, setName] = useState("");
  const [role, setRole] = useState<AccessRole>("reviewer");
  const [pin, setPin] = useState("");
  const [repeat, setRepeat] = useState("");
  const [enrollId, setEnrollId] = useState("");
  const [enrollPin, setEnrollPin] = useState("");
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
    <p>Администратор один. Проверяющих и руководителей проекта можно добавить несколько.</p>
    <table><thead><tr><th>Имя</th><th>Роль</th><th>Вход в программу</th></tr></thead><tbody>{users.map(user => <tr key={user.id}><td>{user.display_name}</td><td>{ROLE_LABELS[user.role]}</td><td>{user.role === "project_manager" ? "После открытия ответственным" : user.can_unlock === false ? <button type="button" className="button secondary" disabled={busy} onClick={() => { setEnrollId(user.id); setEnrollPin(""); }}>Разрешить вход</button> : "Разрешён"}</td></tr>)}</tbody></table>
    {enrollId && <form className="access-card" onSubmit={e => { e.preventDefault(); void enroll(); }}><h3>Разрешить вход: {users.find(user => user.id === enrollId)?.display_name}</h3>
      <p>Для ключа из прежней версии введите его действующий личный код. Затем подтвердите действие кодом администратора.</p>
      <label>Действующий личный код<input type="password" autoComplete="off" maxLength={128} value={enrollPin} disabled={busy} onChange={e => setEnrollPin(e.target.value)} /></label>
      <div className="access-actions"><button type="button" className="button secondary" disabled={busy} onClick={() => { setEnrollId(""); setEnrollPin(""); }}>Отмена</button><button className="button primary" disabled={busy || enrollPin.length < 6}>Разрешить вход с этим кодом</button></div>
    </form>}
    <form className="access-card" onSubmit={e => { e.preventDefault(); void create(); }}><h3>Добавить ответственное лицо</h3>
      <label>Имя ответственного<input required maxLength={120} disabled={busy} value={name} onChange={e => setName(e.target.value)} /></label>
      <label>Роль<select value={role} disabled={busy} onChange={e => setRole(e.target.value as AccessRole)}><option value="reviewer">Проверяющий</option><option value="project_manager">Руководитель проекта</option></select></label>
      <p>{role === "reviewer" ? "Редактирует планы, сведения и значения отчёта. Сохраняет и подтверждает данные своим кодом." : "Заполняет отчёт после открытия программы ответственным лицом. Редактирует планы и сведения, сохраняет и подтверждает данные своим кодом."}</p>
      <label>Личный код (от 6 символов)<input type="password" autoComplete="new-password" minLength={6} maxLength={128} disabled={busy} value={pin} onChange={e => setPin(e.target.value)} /></label>
      <label>Повтор личного кода<input type="password" autoComplete="new-password" minLength={6} maxLength={128} disabled={busy} value={repeat} onChange={e => setRepeat(e.target.value)} /></label>
      <button className="button primary" disabled={busy || !gateway.createReportSigner || !name.trim() || pin.length < 6 || !repeat}>{busy ? "Создаём…" : "Создать ключ ответственного"}</button>
      {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    </form>
  </section>;
}
