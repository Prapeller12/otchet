import { useState } from "react";

import { ReportMatrixPage } from "../pages/report-matrix/ReportMatrixPage";
import { useApplicationGateway } from "./providers/ApplicationGatewayProvider";
import {
  REPORT_TYPES,
  type ReportType,
} from "../shared/api/report-cell-contract";

const REPORT_LABELS: Record<ReportType, string> = {
  DAILY_MOVEMENT: "Ежедневный отчёт",
  HEAD_SITE: "Головная площадка",
  SUBSIDIARY: "Дочерние общества",
};

export function App() {
  const gateway = useApplicationGateway();
  const [reportType, setReportType] = useState<ReportType>("DAILY_MOVEMENT");

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="app-eyebrow">Локальный контур</p>
          <h1>Производственная отчётность</h1>
        </div>
        <div className="connection-state" aria-label="Режим подключения">
          <span aria-hidden="true" />
          {gateway.mode === "pywebview"
            ? "SQLite подключена"
            : "Демонстрация без сохранения на диск"}
        </div>
      </header>

      <nav className="report-tabs" aria-label="Формы отчётности">
        {REPORT_TYPES.map((type) => (
          <button
            className={type === reportType ? "report-tab is-active" : "report-tab"}
            key={type}
            type="button"
            aria-current={type === reportType ? "page" : undefined}
            onClick={() => setReportType(type)}
          >
            {REPORT_LABELS[type]}
          </button>
        ))}
      </nav>

      <main>
        <ReportMatrixPage reportType={reportType} />
      </main>
    </div>
  );
}
