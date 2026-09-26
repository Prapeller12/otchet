import type { DailySummary } from "../../shared/api/application-gateway";
import { HintValue } from "../../shared/ui/FieldHint";
import { dailySummaryHint, identityHint } from "../../shared/config/report-field-hints";
import "./daily-summary.css";

const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
const METRICS = ["Всего получено", "Всего использовано", "Остаток изделий"];

export function DailyMonthlySummary({ summary }: { summary: DailySummary | undefined }) {
  if (!summary?.components.length) return null;
  return <section className="daily-monthly-summary" aria-label="Месячная сводка составных частей">
    <h3>Составные части дочерних обществ — месячная сводка</h3>
    <p>Суммы поступлений и расхода за месяц; остаток — на конец месяца. Пусто: данные не представлены. Все значения рассчитываются автоматически.</p>
    <div className="daily-summary-scroll">
      <table>
        <caption>{summary.year} год</caption>
        <thead>
          <tr><th rowSpan={2} scope="col">Месяц</th>{summary.components.map(component => <th key={component.group_id} colSpan={3} scope="colgroup"><HintValue hint={identityHint("position")}>{component.party} / {component.position}</HintValue></th>)}</tr>
          <tr>{summary.components.flatMap(component => METRICS.map(metric => <th key={`${component.group_id}-${metric}`} scope="col">{metric}</th>))}</tr>
        </thead>
        <tbody>{MONTHS.map((month, index) => <tr key={month}><th scope="row">{month}</th>{summary.components.flatMap(component => component.rows.map(row => <td key={row.row_id}><HintValue hint={dailySummaryHint(row.metric_code)}>{row.monthly[index] || ""}</HintValue></td>))}</tr>)}</tbody>
        <tfoot><tr><th scope="row">Итого за год</th>{summary.components.flatMap(component => component.rows.map(row => <td key={row.row_id}><HintValue hint={dailySummaryHint(row.metric_code, true)}>{row.annual || ""}</HintValue></td>))}</tr></tfoot>
      </table>
    </div>
  </section>;
}
