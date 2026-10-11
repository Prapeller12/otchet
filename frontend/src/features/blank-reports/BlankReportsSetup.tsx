import { useEffect, useState } from "react";
import { useApplicationGateway } from "../../app/providers/ApplicationGatewayProvider";
import type { BlankReportProfiles } from "../../shared/api/application-gateway";
import type { ReportType } from "../../shared/api/report-cell-contract";

type Props = {
  onCompleted: (result: {organization_id: string; year: number; month: number; report_type: ReportType}) => void | Promise<void>;
  onBusyChange?: (busy: boolean) => void;
};

export function BlankReportsSetup({onCompleted, onBusyChange}: Props) {
  const gateway = useApplicationGateway();
  const [profiles, setProfiles] = useState<BlankReportProfiles | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    if (!gateway.listBlankReportProfiles) return;
    gateway.listBlankReportProfiles().then(value => {if (active) setProfiles(value);})
      .catch(reason => {if (active) setError(reason instanceof Error ? reason.message : String(reason));});
    return () => {active = false;};
  }, [gateway]);
  async function create() {
    if (!profiles || !gateway.createBlankReportProfiles || busy) return;
    setBusy(true); onBusyChange?.(true); setError("");
    try {
      const result = await gateway.createBlankReportProfiles({profile_set_id: profiles.profile_set_id});
      const head = result.organizations.find(org => org.report_type === "HEAD_SITE");
      if (!head) throw new Error("В наборе отсутствует головная площадка");
      await onCompleted({organization_id: head.organization_id, report_type: head.report_type, year: 2027, month: 9});
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally {setBusy(false); onBusyChange?.(false);}
  }
  return <section aria-label="Заготовки с сентября 2027" className="blank-reports-setup">
    <h3>Заготовки с сентября 2027</h3>
    <p>КГЭ — головная площадка. Молот, Кулакова, СРЗ и Равенство — дочерние.
      Размеры взяты из приложенных книг. Наименования, обозначения и количества заполните самостоятельно.</p>
    {error && <p role="alert">{error}</p>}
    {profiles ? <table><caption>Структура новых отчётов</caption><thead><tr>
      <th>Площадка</th><th>Позиций</th><th>Производителей</th><th>Строк данных</th>
    </tr></thead><tbody>{profiles.organizations.map(org => <tr key={org.template_id}>
      <th scope="row">{org.label}</th><td>{org.position_count}</td>
      <td>{org.distinct_manufacturer_count}</td><td>{org.display_row_count}</td>
    </tr>)}</tbody></table> : <p>Загрузка размеров заготовок…</p>}
    <p>В программе появятся технические подписи «Позиция … · заполните» и «Производитель … · заполните».
      Строки без производителя сохранятся. Категории и связи изделий с комплектующими задайте в настройках.</p>
    <p>Создание добавляет отдельные отчёты. Повторное нажатие открывает созданный набор и сохраняет внесённые данные.</p>
    <button type="button" disabled={!profiles || busy || !gateway.createBlankReportProfiles} onClick={() => void create()}>
      {busy ? "Создание заготовок…" : "Создать или открыть пять заготовок"}
    </button>
  </section>;
}
