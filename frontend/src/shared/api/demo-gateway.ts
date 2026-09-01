import type {
  ApplicationGateway,
  ExportRequest,
  ExportResult,
  ImportPreview,
  ImportRequest,
  MatrixCellContract,
  MatrixRowContract,
  ReportMatrixContract,
  ReportMatrixQuery,
  SaveReportCellsRequest,
  SaveReportCellsResponse,
} from "./application-gateway";
import type {
  ReportCellAccessState,
  ReportCellCoordinate,
  ReportCellValue,
  ReportType,
} from "./report-cell-contract";

type DemoRowDefinition = {
  id: string;
  groupId: string;
  groupLabel: string;
  indicatorLabel: string;
  subject: { kind: "product" | "component"; id: string };
  indicator: { kind: "metric" | "operation"; code: string };
  access: ReportCellAccessState;
  values: ReadonlyArray<string | null>;
  lockReason?: string;
  errorAt?: number;
};

const WORKING_REFERENCE_REASON =
  "Недоступно до утверждения версии формы и координатной карты.";

const REPORT_TITLES: Record<ReportType, string> = {
  DAILY_MOVEMENT: "Ежедневное движение и остатки",
  HEAD_SITE: "Головная площадка",
  SUBSIDIARY: "Дочерние общества",
};

const DATE_LABELS = ["01.09", "02.09", "03.09", "04.09", "05.09"];
const ISO_DATES = [
  "2026-09-01",
  "2026-09-02",
  "2026-09-03",
  "2026-09-04",
  "2026-09-05",
];

function quantity(value: string | null): ReportCellValue {
  return value === null
    ? { kind: "DATA_NOT_PROVIDED" }
    : { kind: "QUANTITY", quantity: value };
}

function coordinate(
  reportType: ReportType,
  definition: DemoRowDefinition,
  date: string,
): ReportCellCoordinate {
  const subject =
    definition.subject.kind === "product"
      ? { product_id: definition.subject.id }
      : { component_id: definition.subject.id };
  const indicator =
    definition.indicator.kind === "metric"
      ? { metric_code: definition.indicator.code }
      : { operation_type: definition.indicator.code };
  const time =
    reportType === "SUBSIDIARY"
      ? { period_start: date }
      : { operation_date: date };

  return {
    report_type: reportType,
    organization_id: "demo-organization",
    ...subject,
    ...indicator,
    ...time,
  } as ReportCellCoordinate;
}

function rowDefinitions(reportType: ReportType): DemoRowDefinition[] {
  if (reportType === "DAILY_MOVEMENT") {
    return [
      {
        id: "daily-received",
        groupId: "demo-component-a",
        groupLabel: "Демо-поставщик · позиция A",
        indicatorLabel: "Получено",
        subject: { kind: "component", id: "demo-component-a" },
        indicator: { kind: "operation", code: "DEMO_RECEIPT" },
        access: "editable",
        values: ["12", null, "0", "4", null],
      },
      {
        id: "daily-used",
        groupId: "demo-component-a",
        groupLabel: "Демо-поставщик · позиция A",
        indicatorLabel: "Использовано",
        subject: { kind: "component", id: "demo-component-a" },
        indicator: { kind: "operation", code: "DEMO_CONSUMPTION" },
        access: "editable",
        values: ["2", "0", null, "1", null],
        errorAt: 4,
      },
      {
        id: "daily-balance",
        groupId: "demo-component-a",
        groupLabel: "Демо-поставщик · позиция A",
        indicatorLabel: "Остаток",
        subject: { kind: "component", id: "demo-component-a" },
        indicator: { kind: "metric", code: "DEMO_BALANCE" },
        access: "calculated",
        values: ["10", "10", "10", "13", "13"],
      },
      {
        id: "daily-plan",
        groupId: "demo-product-a",
        groupLabel: "Демо-сборочный комплект A",
        indicatorLabel: "План выпуска",
        subject: { kind: "product", id: "demo-product-a" },
        indicator: { kind: "metric", code: "DEMO_RELEASE_PLAN" },
        access: "editable",
        values: ["6", "6", "6", "6", "6"],
      },
      {
        id: "daily-released",
        groupId: "demo-product-a",
        groupLabel: "Демо-сборочный комплект A",
        indicatorLabel: "Выпущено по кодам",
        subject: { kind: "product", id: "demo-product-a" },
        indicator: { kind: "operation", code: "DEMO_RELEASE" },
        access: "editable",
        values: ["4", null, "0", "5", null],
      },
      {
        id: "daily-shipment",
        groupId: "demo-product-a",
        groupLabel: "Демо-сборочный комплект A",
        indicatorLabel: "Отправка",
        subject: { kind: "product", id: "demo-product-a" },
        indicator: { kind: "operation", code: "DEMO_SHIPMENT" },
        access: "locked",
        values: [null, null, null, null, null],
        lockReason: "Демонстрация заблокированной строки.",
      },
      {
        id: "daily-arrival",
        groupId: "demo-product-a",
        groupLabel: "Демо-сборочный комплект A",
        indicatorLabel: "Прибытие",
        subject: { kind: "product", id: "demo-product-a" },
        indicator: { kind: "operation", code: "DEMO_ARRIVAL" },
        access: "editable",
        values: [null, "4", null, null, null],
      },
    ];
  }

  if (reportType === "HEAD_SITE") {
    return [
      {
        id: "head-plan",
        groupId: "demo-head-product-a",
        groupLabel: "Демо-изготовитель · изделие A",
        indicatorLabel: "План выпуска",
        subject: { kind: "product", id: "demo-head-product-a" },
        indicator: { kind: "metric", code: "DEMO_RELEASE_PLAN" },
        access: "editable",
        values: ["20", "20", "20", "20", "20"],
      },
      {
        id: "head-fact",
        groupId: "demo-head-product-a",
        groupLabel: "Демо-изготовитель · изделие A",
        indicatorLabel: "Выпущено по кодам",
        subject: { kind: "product", id: "demo-head-product-a" },
        indicator: { kind: "operation", code: "DEMO_RELEASE" },
        access: "editable",
        values: ["18", "20", "0", null, null],
      },
      {
        id: "head-shipped",
        groupId: "demo-head-product-a",
        groupLabel: "Демо-изготовитель · изделие A",
        indicatorLabel: "Отправка",
        subject: { kind: "product", id: "demo-head-product-a" },
        indicator: { kind: "operation", code: "DEMO_SHIPMENT" },
        access: "editable",
        values: ["10", "8", null, "0", null],
      },
      {
        id: "head-balance",
        groupId: "demo-head-product-a",
        groupLabel: "Демо-изготовитель · изделие A",
        indicatorLabel: "Остаток изделий",
        subject: { kind: "product", id: "demo-head-product-a" },
        indicator: { kind: "metric", code: "DEMO_PRODUCT_BALANCE" },
        access: "calculated",
        values: ["8", "20", "20", "20", "20"],
      },
    ];
  }

  return [
    {
      id: "subsidiary-contract",
      groupId: "demo-subsidiary-component-a",
      groupLabel: "Демо-изготовитель · ДСЕ/ПКИ A",
      indicatorLabel: "Договорной объём",
      subject: { kind: "component", id: "demo-subsidiary-component-a" },
      indicator: { kind: "metric", code: "DEMO_CONTRACT_VOLUME" },
      access: "locked",
      values: ["100", "100", "100", "100", "100"],
      lockReason: "Нормативное значение доступно только для просмотра.",
    },
    {
      id: "subsidiary-plan",
      groupId: "demo-subsidiary-component-a",
      groupLabel: "Демо-изготовитель · ДСЕ/ПКИ A",
      indicatorLabel: "Утверждённый план",
      subject: { kind: "component", id: "demo-subsidiary-component-a" },
      indicator: { kind: "metric", code: "DEMO_APPROVED_PLAN" },
      access: "locked",
      values: ["20", "20", "20", "20", "20"],
      lockReason: "Демонстрация утверждённого значения.",
    },
    {
      id: "subsidiary-supplied",
      groupId: "demo-subsidiary-component-a",
      groupLabel: "Демо-изготовитель · ДСЕ/ПКИ A",
      indicatorLabel: "Поставка",
      subject: { kind: "component", id: "demo-subsidiary-component-a" },
      indicator: { kind: "operation", code: "DEMO_SUPPLY" },
      access: "editable",
      values: ["20", null, "0", "18", null],
    },
    {
      id: "subsidiary-variance",
      groupId: "demo-subsidiary-component-a",
      groupLabel: "Демо-изготовитель · ДСЕ/ПКИ A",
      indicatorLabel: "Профицит / дефицит",
      subject: { kind: "component", id: "demo-subsidiary-component-a" },
      indicator: { kind: "metric", code: "DEMO_CONTRACT_VARIANCE" },
      access: "calculated",
      values: ["0", "-20", "-40", "-42", "-62"],
    },
  ];
}

function createRow(
  reportType: ReportType,
  definition: DemoRowDefinition,
): MatrixRowContract {
  const cells: MatrixCellContract[] = ISO_DATES.map((date, index) => {
    const isError = definition.errorAt === index;
    const value = definition.values[index] ?? null;
    const cell: MatrixCellContract = {
      column_id: `period-${index}`,
      coordinate: coordinate(reportType, definition, date),
      value: quantity(value),
      state: {
        access: definition.access,
        persistence: isError ? "error" : "saved",
      },
    };
    if (definition.lockReason !== undefined) {
      cell.lock_reason = definition.lockReason;
    }
    if (isError) {
      cell.issue = {
        code: "DEMO_VALIDATION_ERROR",
        message: "Демонстрация ошибки: значение требует проверки.",
      };
    }
    return cell;
  });

  return {
    id: definition.id,
    group_id: definition.groupId,
    group_label: definition.groupLabel,
    left_values: {
      subject: definition.groupLabel,
      indicator: definition.indicatorLabel,
    },
    cells,
  };
}

export function createDemoMatrix(reportType: ReportType): ReportMatrixContract {
  return {
    report_type: reportType,
    title: REPORT_TITLES[reportType],
    subtitle: "Обезличенный демонстрационный контур",
    form_status: "WORKING_REFERENCE",
    source_notice:
      "Показана работа интерфейса на синтетических данных. Реальные формы и коды не встроены.",
    matrix_revision: "demo-1",
    left_columns: [
      { id: "subject", label: "Изготовитель / объект", width: 250 },
      { id: "indicator", label: "Показатель", width: 190 },
    ],
    time_columns: DATE_LABELS.map((label, index) => ({
      id: `period-${index}`,
      label,
      group_label: "Сентябрь 2026",
      width: 92,
    })),
    rows: rowDefinitions(reportType).map((definition) =>
      createRow(reportType, definition),
    ),
    capabilities: {
      save: { enabled: true },
      import: { enabled: false, reason: WORKING_REFERENCE_REASON },
      export: { enabled: false, reason: WORKING_REFERENCE_REASON },
    },
    navigation: { enter_direction: "down" },
  };
}

function coordinateKey(value: ReportCellCoordinate): string {
  return JSON.stringify(value);
}

export class DemoGateway implements ApplicationGateway {
  readonly mode = "demo" as const;
  readonly #matrices = new Map<ReportType, ReportMatrixContract>();

  async getReportMatrix(
    query: ReportMatrixQuery,
  ): Promise<ReportMatrixContract> {
    const existing = this.#matrices.get(query.report_type);
    if (existing !== undefined) return structuredClone(existing);

    const matrix = createDemoMatrix(query.report_type);
    this.#matrices.set(query.report_type, matrix);
    return structuredClone(matrix);
  }

  async saveReportCells(
    request: SaveReportCellsRequest,
  ): Promise<SaveReportCellsResponse> {
    const matrix = this.#matrices.get(request.report_type);
    if (matrix === undefined) throw new Error("Demo matrix is not loaded");
    if (matrix.matrix_revision !== request.base_revision) {
      throw new Error("REVISION_CONFLICT: матрица была обновлена");
    }

    const changes = new Map(
      request.changes.map((change) => [coordinateKey(change.coordinate), change]),
    );
    const saved = [];
    for (const row of matrix.rows) {
      for (const cell of row.cells) {
        const change = changes.get(coordinateKey(cell.coordinate));
        if (change === undefined) continue;
        cell.value = change.value;
        cell.state = { access: cell.state.access, persistence: "saved" };
        delete cell.issue;
        saved.push({
          coordinate: cell.coordinate,
          value: cell.value,
          state: cell.state,
        });
      }
    }
    const nextRevision = Number(matrix.matrix_revision.split("-")[1] ?? "1") + 1;
    matrix.matrix_revision = `demo-${nextRevision}`;
    return {
      matrix_revision: matrix.matrix_revision,
      cells: structuredClone(saved),
    };
  }

  async validateImport(_request: ImportRequest): Promise<ImportPreview> {
    throw new Error(WORKING_REFERENCE_REASON);
  }

  async exportReport(_request: ExportRequest): Promise<ExportResult> {
    throw new Error(WORKING_REFERENCE_REASON);
  }
}
