# Контракт матричной ячейки

`report-cell.schema.json` фиксирует только подтверждённую границу между
frontend и application-слоем. Он не является схемой таблицы БД и не использует
Excel-адрес как идентификатор.

## Зафиксировано

- координата содержит `report_type`, `organization_id` и ровно по одному полю
  из каждой пары: `product_id` / `component_id`, `metric_code` /
  `operation_type`, `operation_date` / `period_start`;
- `bom_version_id` передаётся только когда версия BOM влияет на смысл ячейки;
- идентификаторы непрозрачны для frontend и сериализуются непустыми строками;
- даты передаются как ISO 8601 `YYYY-MM-DD`;
- точное количество передаётся десятичной строкой, а не JSON-числом;
- `{ "kind": "DATA_NOT_PROVIDED" }` и
  `{ "kind": "QUANTITY", "quantity": "0" }` — разные значения;
- доступ (`editable`, `calculated`, `locked`) отделён от состояния сохранения
  (`error`, `dirty`, `saving`, `saved`), поскольку эти признаки независимы.

## OPEN: код типа ежедневного отчёта

В `docs/ui-contracts/reference-form-register.md` указан код `DAILY`, а в
DEV-документации v0.5, раздел 9.8, приведён `DAILY_MOVEMENT`. До решения
владельца схема проверяет только формат внешнего uppercase-кода и не вводит
enum, alias или автоматическое преобразование. То же правило применяется к
`metric_code` и `operation_type`: реальные коды должны поступить из
утверждённого контракта, а не из frontend.

JSON Schema использует draft 2020-12. Автономная проверка без установки npm
пакетов:

```text
node frontend/tests/contracts/report-cell-contract.test.mjs
```

Скрипт проверяет структуру схемы, разрешение внутренних `$ref`, положительные и
отрицательные fixtures, календарную корректность ISO-дат и наличие
соответствующих дискриминированных union-типов TypeScript. Дополнительно
`backend/tests/integration/test_report_cell_schema.py` проверяет те же fixtures
стандартным `Draft202012Validator` из `jsonschema`. Компиляция TypeScript должна
быть добавлена после появления согласованного frontend toolchain.
