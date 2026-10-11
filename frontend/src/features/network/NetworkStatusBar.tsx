import type { NetworkStatus } from "../../shared/api/application-gateway";
import "./network.css";

export function NetworkStatusBar({ status, error, notice, blocked, busy, onCheck, onRefresh, onRecover, onRetry }: {
  status: NetworkStatus | null; error: string; notice: string; blocked: boolean; busy: boolean;
  onCheck(): void; onRefresh(): void; onRecover?(): void; onRetry?(): void;
}) {
  if (status?.mode === "local" || (!status && !error)) return null;
  const unknown = status?.state === "outcome_unknown";
  const label = !status ? "Не удалось проверить общее хранилище" : unknown ? "Результат общей записи неизвестен" : status?.state === "unavailable" ? "Общее хранилище недоступно"
    : status?.state === "stale" || status?.snapshot_changed ? "В общих данных есть изменения" : "Общая версия актуальна";
  return <section className="network-status" aria-label="Общие данные">
    <div className="network-message" role="status"><strong>{label}</strong>{status?.message && <span>{status.message}</span>}{notice && <span>{notice}</span>}
      {blocked && <span>Сначала завершите открытое действие или работу с черновиком. Введённые значения остаются в форме.</span>}</div>
    <div className="network-actions">
      <button type="button" className="button secondary" disabled={busy} onClick={onCheck}>Проверить соединение</button>
      <button type="button" className="button secondary" disabled={busy || blocked || unknown} onClick={onRefresh}>Обновить общие данные</button>
      {unknown && status?.pending_operation_id && onRecover && <button type="button" className="button secondary" disabled={busy} onClick={onRecover}>Проверить общую запись</button>}
      {unknown && onRetry && <button type="button" className="button secondary" disabled={busy} onClick={onRetry}>Повторить прежнюю общую запись</button>}
    </div>
    {error && <p role="alert">{error}</p>}
  </section>;
}
