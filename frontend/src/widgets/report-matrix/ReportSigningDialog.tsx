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
  const disabled = busy || loading;
  const selected = users.find(user => user.id === signerId);
  useEffect(() => {
    let active = true;
    void gateway.listReportSigners!().then(result => {
      if (!active) return;
      const reviewers = result.filter(user => user.role === "admin" || user.role === "reviewer");
      setUsers(reviewers); setSignerId(reviewers[0]?.id ?? "");
    }).catch(reason => { if (active) setLocalError(String(reason instanceof Error ? reason.message : reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [gateway]);

  async function sign() {
    const secret = pin;
    setPin(""); setLocalError("");
    await onSign(signerId, secret);
  }
  return <div className="excel-dialog-backdrop"><section className="excel-dialog verification-form" role="dialog" aria-modal="true" aria-labelledby="verification-title">
    <h3 id="verification-title">Подтверждение данных — {month}</h3>
    <p>Подписывается сохранённый отчёт за выбранный месяц вместе со сводными данными с начала года.</p>
    {loading && <p role="status">Загрузка…</p>}
    {!loading && !users.length && <p>Нет проверяющих. Попросите администратора добавить проверяющего в разделе «Пользователи и ключи».</p>}
    {!!users.length && <>
      <label>Подтверждающий пользователь<select autoFocus value={signerId} disabled={disabled} onChange={e => { setSignerId(e.target.value); setPin(""); setConfirmed(false); }}>
        {users.map(user => <option key={user.id} value={user.id}>{user.display_name} · {user.key_fingerprint}</option>)}
      </select></label>
      {selected && <p>Ключ проверяющего: {selected.key_fingerprint}</p>}
      <label>Код проверяющего или администратора<input type="password" autoComplete="off" maxLength={128} value={pin} disabled={disabled} onChange={e => setPin(e.target.value)} /></label>
      <label><input type="checkbox" checked={confirmed} disabled={disabled} onChange={e => setConfirmed(e.target.checked)} /> Я проверил данные и подтверждаю их верность.</label>
      <p>Внутренняя криптографическая подпись данных. В PDF печатаются две строки с автором, временем, отпечатком ключа и подписью.</p>
    </>}
    {(localError || error) && <p role="alert" style={{ whiteSpace: "pre-wrap", maxHeight: "25vh", overflowY: "auto" }}>{localError || error}</p>}
    <div className="excel-dialog-actions">
      <button className="button secondary" disabled={disabled} onClick={onClose}>Отмена</button>
      {!!users.length && <button className="button primary" disabled={disabled || !confirmed || !signerId || pin.length < 6} onClick={() => void sign()}>{busy ? "Подтверждение…" : "Подтверждаю верность данных"}</button>}
    </div>
  </section></div>;
}
