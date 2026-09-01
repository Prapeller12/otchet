# Трассировка требований первого инкремента

Дата проверки: 01.09.2026.
Область: доменный фундамент и сквозной технический preview.

| Требование | Источник | Реализация | Проверка | Статус |
|---|---|---|---|---|
| Пустое значение не равно подтверждённому нулю | `AGENTS.md` 4.2; DEV v0.5, строка «Пусто и ноль» | `backend/domain/calculations.py`; `report-cell.schema.json` | `TestQuantityValue`; valid/invalid contract fixtures | `VERIFIED` |
| Остаток считается из отдельных операций | `AGENTS.md` 4.3 и 6.1 | `calculate_stock` | `TestStock` | `VERIFIED` |
| Потребность учитывает план, входимость и потери | `AGENTS.md` 6.2 | `calculate_required_quantity` | `TestRequiredQuantity` | `VERIFIED` |
| Комплектность — минимум целых комплектов по обязательным компонентам | `AGENTS.md` 6.3; DEV v0.5, 6.3 | `calculate_readiness` | `TestReadiness` | `VERIFIED` |
| Контрольные значения готовности `23` и `30` | `AGENTS.md` 18; DEV v0.5 | `calculate_readiness` | два теста с полным контрольным набором | `VERIFIED` |
| Отсутствующий обязательный компонент даёт 0 и входит в bottlenecks | DEV v0.5, таблица unit-сценариев | `calculate_readiness` | `test_missing_mandatory_component_yields_zero_and_is_bottleneck` | `VERIFIED` |
| Вернуть все одинаковые bottlenecks | `AGENTS.md` 6.3 | `calculate_readiness` | `test_returns_all_equal_bottlenecks_in_input_order` | `VERIFIED` |
| Дефицит и договорное отклонение | `AGENTS.md` 6.4 | `calculate_shortage_to_target`; `calculate_contract_variance` | `TestShortageAndContractVariance` | `VERIFIED` |
| При нулевом плане процент не рассчитывается | `AGENTS.md` 6.5 | `calculate_completion_rate` | `TestCompletionRate` | `VERIFIED` |
| Количества без binary float | `AGENTS.md` 11; DEV v0.5 | `Decimal`; десятичные строки в SQLite и JSON | unit, integration и contract-тесты | `VERIFIED` |
| Дата/время операции отделены от времени создания записи | `AGENTS.md` 11; DEV v0.5, `op_at` и пример API | `operation_at` с timezone; отдельный `created_at` | integration-тесты валидных и невалидных ISO timestamps | `VERIFIED` |
| BOM версионируется; утверждённые версии неизменяемы и не пересекаются по периоду | `AGENTS.md` 4.3 и 11; DEV v0.5 | `bom_versions`, `bom_items`; SQLite triggers | тесты пересечения, изменения и удаления утверждённой версии | `VERIFIED` |
| Проведённая операция неизменяема; исправление — сторно с причиной | `AGENTS.md` 4.3 и 11 | таблицы и triggers `stock_operations` / `product_operations` | integration-тесты изменения, удаления, повторного и цепочного сторно | `VERIFIED` |
| Миграции последовательны, атомарны и не редактируются после применения | `AGENTS.md` 11 | `backend/infrastructure/database/migrator.py` | тесты пустой БД, rollback, SHA-256 и непрерывного префикса | `VERIFIED` |
| Ячейка адресуется бизнес-координатой, а не Excel-адресом | DEV v0.5, 9.8; `AGENTS.md` 7 | JSON Schema и TypeScript union | Node contract-test | `VERIFIED` |
| Тип отчёта ограничен утверждёнными кодами `DAILY_MOVEMENT`, `HEAD_SITE`, `SUBSIDIARY` | DEV v0.5, 9.8 | JSON Schema enum и TypeScript literal union | valid/invalid contract fixtures | `VERIFIED` |
| Технические логи, телеметрия и внешняя сеть выключены по умолчанию | `AGENTS.md` 4.4 | `config/app.defaults.toml`; repository verifier | `scripts/verify_repository.py` | `VERIFIED_DEFAULTS_ONLY` |
| Даты в матрицах расположены горизонтально | `AGENTS.md` 4.2 и 5 | React/Vite matrix + versioned descriptors | frontend unit tests и production build | `VERIFIED_PREVIEW` |
| Пусто и подтверждённый ноль переживают перезапуск | `AGENTS.md` 4.2 | PyWebView bridge → application-service → SQLite | integration round-trip `empty → 0 → reload → empty` | `VERIFIED_PREVIEW` |
| Неподтверждённый Excel import/export не может быть запущен | правила `OPEN` | capabilities + bridge fail closed | integration test кода `TEMPLATE_CONTRACT_NOT_APPROVED` | `VERIFIED_PREVIEW` |
| Роли, закрытие периода и производственные справочники | `AGENTS.md` 11–15 | не реализуются без утверждённых контрактов | отсутствует | `OPEN` |
| Portable ZIP на чистой Windows 10/11 | `AGENTS.md` 4.1 и 18 | launcher и fail-closed build/package/verify scripts | unit tests на Linux; Windows acceptance отсутствует | `PARTIAL / OPEN` |

`VERIFIED` означает прохождение доступной автоматической проверки в этом репозитории, а не
приёмку владельцем программы. DEV v0.5 утверждена владельцем 31.08.2026; рабочие формы
требуют отдельного подтверждения статуса.
