import { RecoveryBackups } from "./RecoveryBackups";
import { UiIcon } from "../../shared/ui/UiIcon";
import { useEffect, useState, type ReactNode } from "react";
import type { AccessStatus, ApplicationGateway, NetworkStatus } from "../../shared/api/application-gateway";
import "./access.css";

export const ROLE_LABELS = { admin: "Администратор", reviewer: "Проверяющий", project_manager: "Руководитель проекта" };

export function AccessGate({ gateway, children, reloadKey = 0 }: { gateway: ApplicationGateway; children: ReactNode | ((startupNotice: ReactNode) => ReactNode); reloadKey?: number }) {
  const [status, setStatus] = useState<AccessStatus | null>(null);
  const [name, setName] = useState("");
  const [identity, setIdentity] = useState("");
  const [pin, setPin] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [recoveryOpen, setRecoveryOpen] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [networkStatus, setNetworkStatus] = useState<NetworkStatus | null>(null);
  const [activationWarning, setActivationWarning] = useState("");
  const [startupResolution, setStartupResolution] = useState(false);
  const [allowWorkspace, setAllowWorkspace] = useState(false);
  const [keyholderRequired, setKeyholderRequired] = useState(false);
  const [keyholderId, setKeyholderId] = useState("");
  const [keyholderPin, setKeyholderPin] = useState("");
  const [recoveryNotice, setRecoveryNotice] = useState("");
  useEffect(() => {
    const changed = (event: Event) => setNetworkStatus((event as CustomEvent<NetworkStatus>).detail);
    window.addEventListener("network-status-changed", changed);
    return () => window.removeEventListener("network-status-changed", changed);
  }, []);
  useEffect(() => {
    let active = true;
    if (!gateway.getAccessStatus) { setStatus({ state: "ready", users: [] }); setAllowWorkspace(true); return; }
    setError(""); setBusy(true);
    void (async () => {
      const network = await gateway.getNetworkStatus?.();
      if (active && network) setNetworkStatus(network);
      if (network?.mode === "network" && ["unavailable", "outcome_unknown"].includes(network.state)) {
        if (network.state === "outcome_unknown") {
          try {
            const recovery = await gateway.getNetworkRecoveryUsers?.();
            if (active && recovery?.users.length) {
              setStatus({ state: "locked", users: recovery.users }); setIdentity(recovery.users[0]?.id ?? "");
              setKeyholderId(recovery.users.find(user => ["admin", "reviewer"].includes(user.role) && !user.revoked)?.id ?? "");
            }
          } catch { /* Recovery with PIN alone is restricted by backend to pristine setup/transfer. */ }
        }
        throw new Error(network.message ?? "Общее хранилище недоступно. Проверьте подключение и повторите открытие.");
      }
      return gateway.getAccessStatus!();
    })().then(next => {
      if (!active) return;
      setStatus(next); setAllowWorkspace(next.state === "ready"); setIdentity(next.users.find(user => user.role === "admin")?.id ?? next.users[0]?.id ?? "");
    }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [gateway, loadAttempt, reloadKey]);
  if (status?.state === "ready" && allowWorkspace) {
    const notice = status.automatic_open_error ? <p className="access-startup-notice" role="status">{status.automatic_open_error} Отчёты открыты. При следующем запуске может снова понадобиться код.</p> : null;
    return <>{typeof children === "function" ? children(notice) : <>{notice}{children}</>}</>;
  }
  const first = status?.state === "setup" || (status?.state === "legacy" && !status.users.length);
  const legacy = status?.state === "legacy";
  const users = legacy ? status.users.filter(user => user.role === "admin") : status?.users ?? [];
  const networkBlocked = networkStatus?.state === "outcome_unknown" || networkStatus?.state === "unavailable";
  async function submit() {
    if (activationWarning || startupResolution || networkBlocked) return;
    if (first && pin !== repeat) { setError("Коды не совпадают. Введите один и тот же код дважды."); return; }
    setBusy(true); setError("");
    const secret = pin; setPin(""); setRepeat("");
    try {
      const next = first ? await gateway.setupAccess!({ display_name: name.trim(), pin: secret })
        : legacy ? await gateway.setupAccess!({ signer_id: identity, pin: secret })
        : await gateway.unlockAccess!({ signer_id: identity, pin: secret });
      if (next.local_activation_warning) setActivationWarning(next.local_activation_warning);
      else { setStatus(next); setAllowWorkspace(next.state === "ready"); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  async function transferLocalReports() {
    if (!gateway.importLocalStore || !first || networkStatus?.mode !== "network" || busy || activationWarning || networkBlocked) return;
    const secret = pin; setPin(""); setRepeat(""); setBusy(true); setError("");
    try {
      const next = await gateway.importLocalStore(secret);
      if ("cancelled" in next) return;
      if (next.local_activation_warning) setActivationWarning(next.local_activation_warning);
      else { setStatus(next); setAllowWorkspace(next.state === "ready"); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Локальные отчёты не перенесены."); }
    finally { setBusy(false); }
  }
  async function acceptPublishedData() {
    setBusy(true); setError("");
    try {
      await gateway.refreshNetworkData!({ has_unsaved_changes: false, has_unknown_outcome: false });
      const next = await gateway.getAccessStatus!(); setStatus(next); setAllowWorkspace(next.state === "ready"); setActivationWarning(""); setStartupResolution(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось обновить локальные данные."); }
    finally { setBusy(false); }
  }
  async function recoverStartupOperation() {
    const operationId = networkStatus?.pending_operation_id;
    if (!operationId || !gateway.recoverNetworkSetupOperation || busy) return;
    const secret = pin; setPin(""); setRepeat(""); setBusy(true); setError("");
    try {
      const result = await gateway.recoverNetworkSetupOperation(operationId, secret, identity || undefined);
      if (result.data_state === "unknown") { setError("Результат не найден. Это не подтверждает откат; новая запись заблокирована."); return; }
      setStartupResolution(true);
      if (result.data_state === "committed") setActivationWarning("Результат прежней записи подтверждён в общем хранилище.");
      else setError(result.result?.error?.message ?? "Прежняя запись не выполнена. Обновите принятый снимок перед новой попыткой.");
    } catch (reason) {
      if (reason instanceof Error && "code" in reason && reason.code === "NETWORK_RECOVERY_UNLOCK_REQUIRED") setKeyholderRequired(true);
      setError(reason instanceof Error ? reason.message : "Результат прежней записи не проверен.");
    }
    finally { setBusy(false); }
  }
  async function unlockRecoverySnapshot() {
    if (!gateway.unlockNetworkRecovery || busy || !keyholderId) return;
    const secret = keyholderPin; setKeyholderPin(""); setBusy(true); setError("");
    try {
      await gateway.unlockNetworkRecovery({ signer_id: keyholderId, pin: secret });
      setKeyholderRequired(false); setRecoveryNotice("Снимок открыт для проверки. Теперь снова введите код точного владельца прежней записи.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Снимок для проверки не открыт."); }
    finally { setBusy(false); }
  }
  return <main className="access-page"><form className="access-card" onSubmit={event => { event.preventDefault(); void submit(); }}>
    <p className="access-eyebrow">Производственная отчётность</p>
    <h1>{networkStatus?.state === "outcome_unknown" ? "Проверить прежнюю общую запись" : !status ? "Открываем отчёты" : first ? "Первый запуск" : legacy ? "Защитить существующие данные" : "Настроить открытие без кода"}</h1>
    {!status && !error && <p role="status">Открываем сохранённые данные…</p>}
    {recoveryNotice && <p role="status">{recoveryNotice}</p>}
    {activationWarning && <><p role="status">Сохранено на сервере; обновите локальные данные. {activationWarning}</p><button type="button" className="button primary" disabled={busy} onClick={() => void acceptPublishedData()}>Обновить локальные данные</button></>}
    {startupResolution && !activationWarning && <button type="button" className="button primary" disabled={busy} onClick={() => void acceptPublishedData()}>Обновить локальные данные</button>}
    {status && !activationWarning && !startupResolution && <>
      <p>{first ? "Создайте единственного администратора. Он настраивает формы и выдаёт ключи ответственным лицам." : legacy ? "Введите код действующего администратора. Программа сохранит отчёты и включит защиту базы." : "Один раз введите действующий код проверяющего или администратора. Затем отчёты будут открываться автоматически под этой учётной записью Windows. Код ответственного по-прежнему нужен при сохранении и печати."}</p>
      {status.automatic_open_error && <p role="status">{status.automatic_open_error}</p>}
      {first ? <label>Имя администратора<input autoFocus required maxLength={120} value={name} disabled={busy} onChange={e => setName(e.target.value)} autoComplete="name" /></label>
        : <label>Пользователь<select value={identity} disabled={busy} onChange={e => { setIdentity(e.target.value); setPin(""); }}>{users.map(user => <option key={user.id} value={user.id}>{user.display_name} · {ROLE_LABELS[user.role]}</option>)}</select></label>}
      <label>{first ? "Код администратора (от 6 символов)" : "Код доступа"}<input type="password" required minLength={6} maxLength={128} autoComplete={first ? "new-password" : "off"} value={pin} disabled={busy} onChange={e => setPin(e.target.value)} /></label>
      {first && <><label>Повторите код<input type="password" required minLength={6} maxLength={128} autoComplete="new-password" value={repeat} disabled={busy} onChange={e => setRepeat(e.target.value)} /></label><p>Запишите код и храните его в надёжном месте отдельно от программы. Код понадобится для сохранения, печати и открытия данных на другом компьютере.</p></>}
      <button className="button primary" disabled={busy || networkBlocked || pin.length < 6 || (first ? !name.trim() || !repeat : !identity)}><UiIcon name="key" />{busy ? "Открываем…" : first ? "Создать администратора и начать" : legacy ? "Включить защиту и открыть" : "Открыть отчёты"}</button>
      {status.state === "setup" && networkStatus?.mode === "network" && !networkStatus.loaded_global_revision && gateway.importLocalStore && <>
        <p>Для переноса введите код администратора существующих отчётов и выберите их защищённую базу. Исходная папка сохранится.</p>
        <button type="button" className="button secondary" disabled={busy || networkBlocked || pin.length < 6} onClick={() => void transferLocalReports()}>Перенести существующие локальные отчёты</button>
      </>}
    </>}
    {networkStatus?.state === "outcome_unknown" && networkStatus.pending_operation_id && gateway.recoverNetworkSetupOperation && !startupResolution && <>
      <p>Результат прежней записи неизвестен. Введите действующий код её владельца. Новая запись и обновление данных заблокированы до проверки результата.</p>
      {!status && <label>Код администратора<input type="password" autoComplete="off" maxLength={128} value={pin} disabled={busy} onChange={event => setPin(event.target.value)} /></label>}
      <button type="button" className="button secondary" disabled={busy || keyholderRequired || pin.length < 6} onClick={() => void recoverStartupOperation()}>Проверить результат прежней записи</button>
    </>}
    {keyholderRequired && gateway.unlockNetworkRecovery && <>
      <p>Для расшифрования снимка нужен отдельный действующий код администратора или проверяющего. Этот шаг открывает только проверку результата записи.</p>
      <label>Пользователь для открытия снимка<select value={keyholderId} disabled={busy} onChange={event => { setKeyholderId(event.target.value); setKeyholderPin(""); }}>{users.filter(user => !user.revoked && ["admin", "reviewer"].includes(user.role)).map(user => <option key={user.id} value={user.id}>{user.display_name} · {ROLE_LABELS[user.role]}</option>)}</select></label>
      <label>Код для открытия снимка<input type="password" autoComplete="off" maxLength={128} value={keyholderPin} disabled={busy} onChange={event => setKeyholderPin(event.target.value)} /></label>
      <button type="button" className="button secondary" disabled={busy || !keyholderId || keyholderPin.length < 6} onClick={() => void unlockRecoverySnapshot()}>Открыть снимок для проверки</button>
    </>}
    {error && <p role="alert">{error}</p>}
    {!status && error && <button type="button" className="button primary" disabled={busy} onClick={() => setLoadAttempt(value => value + 1)}>Повторить открытие</button>}
  </form>{gateway.listRecoveryBackups && <button type="button" className="button secondary" disabled={busy} onClick={() => setRecoveryOpen(true)}>Восстановить из резервной копии</button>}{recoveryOpen && <RecoveryBackups gateway={gateway} onClose={() => setRecoveryOpen(false)} />}</main>;
}
