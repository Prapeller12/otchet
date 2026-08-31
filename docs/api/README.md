# API

REST/IPC API находится в статусе `OPEN`. Реализован только транспортно-независимый контракт
одной матричной ячейки:

- JSON Schema: `resources/schemas/report-cell/report-cell.schema.json`;
- TypeScript: `frontend/src/shared/api/report-cell-contract.ts`;
- модель frontend-сущности: `frontend/src/entities/report-cell/`.

Координата использует устойчивые идентификаторы, а значение явно различает
`DATA_NOT_PROVIDED` и точную десятичную строку, включая подтверждённый `0`.

Подтверждено: централизованный API использует префикс `/api/v1`, ISO 8601 для дат, точное представление количеств, единый формат ошибок и проверку роли/организации на backend. Локальный bridge и REST должны вызывать одни application-сервисы.
