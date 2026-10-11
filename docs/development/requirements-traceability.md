# Трассировка требований первого инкремента

Дата проверки: 02.09.2026.
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
| Excel round-trip не угадывает координаты | указание владельца 02.09.2026; правила `OPEN` | каноническая книга со скрытой картой; staging + preview + confirm + backup | integration round-trip для трёх форм, foreign workbook rejection; frontend action test | `VERIFIED_PREVIEW` |
| Роли, закрытие периода и производственные справочники | `AGENTS.md` 11–15 | не реализуются без утверждённых контрактов | отсутствует | `OPEN` |
| Portable ZIP на чистой Windows 10/11 | `AGENTS.md` 4.1 и 18 | launcher и fail-closed build/package/verify scripts | unit tests на Linux; Windows acceptance отсутствует | `PARTIAL / OPEN` |

`VERIFIED` означает прохождение доступной автоматической проверки в этом репозитории, а не
приёмку владельцем программы. DEV v0.5 утверждена владельцем 31.08.2026; рабочие формы
требуют отдельного подтверждения статуса.


## Дополнение dev.27 — прямые требования владельца

| Требование | Реализация | Проверка |
|---|---|---|
| Ответственные редактируют поля и планы | report-settings, responsible access policy | access-workspace, responsible_access_dev27, реальное окно |
| Нет повторного кода при создании пользователя | backend admin session, end_administration | responsible_access_dev27, access-workspace |
| Закрепить вкладки | workspace-navigation sticky | геометрия при прокрутке настоящего окна |
| Один накопительный итог | daily_summary.through_month, выбранный месяц | test_daily_summary, daily-cumulative |
| Печать рядом с сохранением, автоматическое подтверждение | общий toolbar, report_confirmation | automatic_confirmation_dev27, entry-workspace, PDF реального окна |

Подробный контракт и принятые границы: ADR-0018. Статус результатов конкретного
выпуска фиксируется прошедшими проверками CI; закрытие производственных периодов
этим инкрементом не добавляется.


## Дополнение dev.34 — поручение от 2026-10-11

| Требование | Реализация | Доказательство |
|---|---|---|
| Единый черновик, ревизия и полный откат | report_draft + shared SQLite UoW | test_report_draft_dev34, независимые проверки отказов |
| Восстановление ответа и защита повтора | idempotency_records, owner-scoped query | reopen / replay / actor conflict tests |
| Диалог перехода и отдельные статусы | ReportMatrix, DraftTransitionDialog | unified-draft-dev34, gateway guards |
| Поиск сохраняет обязательные строки | Highlight + nextmatch | canonical-row assertions |
| PDF и просмотр всего импорта | SavedReportPdfDialog, import review | PDF revision / frontend import tests |
| КГЭ и четыре дочерние, сентябрь 2027 | blank profiles port + SQLite transaction | test_blank_profiles_dev34, source-dimension review |
| Пустые значения и пустые слоты | UNSPECIFIED + empty_supplier_slots | все позиции видны, нет вымышленных количеств |
| Запрет записи до начала нового набора | report_start_period + draft/legacy/import guards | start-period / secure bootstrap tests |
| Коммит сборки | SHA tag + release-manifest metadata | CI source_commit/run_id, manifest validation |
| Пользователи и Windows 10/11 | protocol alpha-dev34 | NOT_RUN; производственная приёмка открыта |

## Дополнение dev.35 — границы рабочего окна

| Требование | Источник | Реализация и проверка |
|---|---|---|
| Таблицы не выходят за рабочее поле | снимок и сообщение владельца 2026-10-11 | ограниченные контейнеры frontend; геометрия реального WebView2 |
| Длинный диалог и его действия доступны | то же сообщение; AGENTS.md 5.4 | ограничение диалога, локальная прокрутка; native dialog bounds |
| Само окно помещается на рабочем экране | то же сообщение | launcher work-area/DPI bounds; test_window_geometry |
| Порядок строк и периодов сохраняется | AGENTS.md 4.2, 5 | прежние определения форм; frontend-регрессия и сравнение строк |
| Закрепления не закрывают числа | сообщение владельца; AGENTS.md 5.4 | доступный префикс идентификаторов по ширине; проверка последних main/aux ячеек и сохранённых ширин |
| Windows 10/11 при фактическом DPI | AGENTS.md 18 | alpha-dev35; отдельная проверка, результат не подменяется CI |

## Дополнение dev.36 — серверная схема указанной беседы

| Требование | Источник | Реализация и доказательство |
|---|---|---|
| EXE из общей папки, клиент локально | прямое указание 11.10.2026; ADR-0025 | native bootstrap; Windows UNC/mapped/profile smoke |
| SQLite никогда не открывается через SMB | AGENTS.md 4.1; ADR-0025 | локальный SQLCipher + immutable encrypted generations |
| CAS до аудита, полный откат, UNKNOWN | единый черновик dev34; ADR-0025 | SnapshotCoordinator; двухклиентные fault/replay тесты |
| Общая версия стабильна до явного обновления | ADR-0025 | matrix.global_revision, draft baseline, refresh guards |
| Источник импорта доступен другому клиенту | ADR-0025; AGENTS.md 13 | migration0018 encrypted BLOB + typed repository, hash checks |
| Receipt атомарен с эффектом | ADR-0025 | migration0019, durable owner/request identity без PIN digest |
| Прежние роли, подписи и формы | AGENTS.md 4.2/15 | актуальная авторизация перед записью, полная регрессия |
| Доказательства конкретной сборки | AGENTS.md 19/20 | manifest commit, CI per OS, ZIP checksums |
| Целевая эксплуатация | AGENTS.md 18 | физические Windows10/11/SMB/DPAPI/10 users NOT_RUN |
