# IPC: единый черновик отчёта

Реализация dev.34, ADR-0023. Координаты и значения сохраняют контракт
`resources/schemas/report-cell/report-cell.schema.json`.
Схема запроса: `resources/schemas/report-draft/save-report-draft.schema.json`.

## `save_report_draft`

Вход: `report_type`, `organization_id`, `year`, `confirmation: {year?, month,
week_start?}`, `base_revision`, `idempotency_key`, `changes: [{coordinate, value}]`,
`presentation?`, `authorization: {signer_id, pin}`. Автор берётся исключительно
из свежей проверки кода backend. Код не сохраняется в результате, аудите или
идемпотентной записи.

`presentation` принимает `title`, `widths`, `header`, `plans`, `actuals`,
`production_code_plans`, `production_code_actuals`, `confirm_production_totals`.
Планы и выпуск: `{YYYY-MM: строка количества}` только выбранного месяца.
По существующим кодам: `{code_id: {YYYY-MM: строка количества}}`. Пустая строка
сохраняет отсутствие количества, `"0"` — подтверждённый ноль. Структурный список
`production_codes`, позиции и организации в этот запрос не входят.

Результат `ok: true` содержит `data_state: committed`, `cells`, `presentation`,
`operation_revision` записанного пакета и `current_revision` текущего отчёта
(`matrix_revision` — совместимый псевдоним текущего токена), `idempotent_replay`.
`verification_state`: `verified`, `verification_error`, `pending` или `stale`.
`backup_state`: `complete`, `post_backup_warning` или `pending`. Существующие
`verification`, `verification_error`, `backup_complete`, `backup_warning`
остаются доступны. `pending` означает, что результат последующего этапа не
зафиксирован, например после аварии между commit и фиксацией результата.

Ошибки до commit: `REVISION_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `VALIDATION_ERROR`,
`DATA_INTEGRITY_ERROR`, `DATABASE_ERROR`; свежий код и предварительная копия
проверяются безопасной оболочкой (`ACCESS_DENIED`). Ошибка подтверждения commit:
`SAVE_OUTCOME_UNKNOWN`; черновик нельзя выдавать за несохранённый пакет или
повторять с новым ключом. Потеря транспортного ответа также имеет неизвестный
исход. Ошибки после commit возвращаются как успешная запись с отдельным статусом.

Одинаковый ключ, автор и пакет возвращают исходный результат без новых фактов,
аудита данных или подписи, даже если базовая ревизия уже устарела. Изменённый
пакет требует нового ключа. UI не переносит подпись старой операции на новый
отчёт: разные ревизии дают `stale`.

## `get_report_draft_save_result`

Вход: тот же `report_type`, `organization_id`, `year`, `confirmation`,
`idempotency_key` и свежая `authorization` автора операции. Запрос только читает;
не создаёт копий, фактов, подписей, аудита доступа или новых полномочий.
Другой автор или контекст получает `RECOVERY_ERROR` без чужого результата.

Найденная операция возвращает сохранённый результат с текущей ревизией.
Отсутствие записи возвращает `ok: true, data: {data_state: unknown,
reason: result_not_found}`. Отсутствие не доказывает откат исходного запроса.
После неизвестного исхода повторное исполнение допустимо только с тем же
неизменённым пакетом и ключом и новым вводом личного кода.

## Проверки

`backend/tests/integration/test_report_draft_dev34.py` проверяет общую транзакцию,
последнее действие пакета, ревизию, повторы, восстановление, роли, пустоту/ноль
и независимые от записи ошибки подписи/резервирования. Закрытие периода в
исходной версии не реализовано отдельной доменной моделью; этот инкремент не
изобретает такую модель. Начальный период созданных заготовок проверяется
отдельно от состояния закрытия периода.
