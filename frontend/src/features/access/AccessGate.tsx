import { RecoveryBackups } from "./RecoveryBackups";
import { UiIcon } from "../../shared/ui/UiIcon";
import { useEffect, useState, type ReactNode } from "react";
import type { AccessStatus, ApplicationGateway } from "../../shared/api/application-gateway";
import "./access.css";

export const ROLE_LABELS = { admin: "Администратор", reviewer: "Проверяющий", project_manager: "Руководитель проекта" };

export function AccessGate({ gateway, children }: { gateway: ApplicationGateway; children: ReactNode }) {
  const [status, setStatus] = useState<AccessStatus | null>(null);
  const [name, setName] = useState("");
  const [identity, setIdentity] = useState("");
  const [pin, setPin] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [recoveryOpen, setRecoveryOpen] = useState(false);
  useEffect(() => {
    let active = true;
    if (!gateway.getAccessStatus) { setStatus({ state: "ready", users: [] }); return; }
    void gateway.getAccessStatus().then(next => {
      if (!active) return;
      setStatus(next); setIdentity(next.users.find(user => user.role === "admin")?.id ?? next.users[0]?.id ?? "");
    }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { active = false; };
  }, [gateway]);
  if (status?.state === "ready") return <>{status.automatic_open_error && <p className="access-startup-notice" role="status">{status.automatic_open_error} Отчёты открыты. При следующем запуске может снова понадобиться код.</p>}{children}</>;
  const first = status?.state === "setup" || (status?.state === "legacy" && !status.users.length);
  const legacy = status?.state === "legacy";
  const users = legacy ? status.users.filter(user => user.role === "admin") : status?.users ?? [];
  async function submit() {
    if (first && pin !== repeat) { setError("Коды не совпадают. Введите один и тот же код дважды."); return; }
    setBusy(true); setError("");
    const secret = pin; setPin(""); setRepeat("");
    try {
      const next = first ? await gateway.setupAccess!({ display_name: name.trim(), pin: secret })
        : legacy ? await gateway.setupAccess!({ signer_id: identity, pin: secret })
        : await gateway.unlockAccess!({ signer_id: identity, pin: secret });
      setStatus(next);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  return <main className="access-page"><form className="access-card" onSubmit={event => { event.preventDefault(); void submit(); }}>
    <p className="access-eyebrow">Производственная отчётность</p>
    <h1>{!status ? "Открываем отчёты" : first ? "Первый запуск" : legacy ? "Защитить существующие данные" : "Настроить открытие без кода"}</h1>
    {!status && !error && <p role="status">Открываем сохранённые данные…</p>}
    {status && <>
      <p>{first ? "Создайте единственного администратора. Он настраивает формы и выдаёт ключи ответственным лицам." : legacy ? "Введите код действующего администратора. Программа сохранит отчёты и включит защиту базы." : "Один раз введите действующий код проверяющего или администратора. Затем отчёты будут открываться автоматически под этой учётной записью Windows. Код ответственного по-прежнему нужен при сохранении и печати."}</p>
      {status.automatic_open_error && <p role="status">{status.automatic_open_error}</p>}
      {first ? <label>Имя администратора<input autoFocus required maxLength={120} value={name} disabled={busy} onChange={e => setName(e.target.value)} autoComplete="name" /></label>
        : <label>Пользователь<select value={identity} disabled={busy} onChange={e => { setIdentity(e.target.value); setPin(""); }}>{users.map(user => <option key={user.id} value={user.id}>{user.display_name} · {ROLE_LABELS[user.role]}</option>)}</select></label>}
      <label>{first ? "Код администратора (от 6 символов)" : "Код доступа"}<input type="password" required minLength={6} maxLength={128} autoComplete={first ? "new-password" : "off"} value={pin} disabled={busy} onChange={e => setPin(e.target.value)} /></label>
      {first && <><label>Повторите код<input type="password" required minLength={6} maxLength={128} autoComplete="new-password" value={repeat} disabled={busy} onChange={e => setRepeat(e.target.value)} /></label><p>Запишите код и храните его в надёжном месте отдельно от программы. Код понадобится для сохранения, печати и открытия данных на другом компьютере.</p></>}
      <button className="button primary" disabled={busy || pin.length < 6 || (first ? !name.trim() || !repeat : !identity)}><UiIcon name="key" />{busy ? "Открываем…" : first ? "Создать администратора и начать" : legacy ? "Включить защиту и открыть" : "Открыть отчёты"}</button>
    </>}
    {error && <p role="alert">{error}</p>}
  </form>{gateway.listRecoveryBackups && <button type="button" className="button secondary" disabled={busy} onClick={() => setRecoveryOpen(true)}>Восстановить из резервной копии</button>}{recoveryOpen && <RecoveryBackups gateway={gateway} onClose={() => setRecoveryOpen(false)} />}</main>;
}
