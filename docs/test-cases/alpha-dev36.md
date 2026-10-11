# Приёмка dev36: файловый сервер и локальный клиент

Основание: ADR-0025, прямое указание владельца 11.10.2026.
База: dev35@08b3ef9c87b4ee4217f61a89bb33076746b46037.

| Проверка | Автоматическое доказательство | Производственная приёмка |
|---|---|---|
| UNC запуск до Python, подготовка и реальное окно | native bootstrap + CI SMB screenshot | целевые Windows 10/11 NOT_RUN |
| Проверяемый кэш, тёплый запуск и повреждение | manifest hashes, отдельная новая копия | физическая сеть NOT_RUN |
| Сетевой диск и перенаправленный профиль | canonical identity, local TEMP fallback | реальные политики профилей NOT_RUN |
| Два клиента, stale CAS, стабильная таблица | packaged SQLCipher SMB smoke + integration | десять клиентов NOT_RUN |
| Полная зашифрованная пара | hashes, SQLCipher integrity only locally | серверные права/резервирование NOT_RUN |
| Writer lock отдельного процесса | packaged child OS handle contention/release | аварийный разрыв физического SMB NOT_RUN |
| Сбой до публикации и потеря ACK | fault injection, rollback, durable receipt | физический обрыв сети NOT_RUN |
| Повтор с тем же владельцем/пакетом | authoritative receipt без второго поколения | приёмка представителями пользователей NOT_RUN |
| Первичная настройка и явный перенос | fresh admin PIN, source pair preserved | реальные рабочие базы NOT_RUN |
| Импорт с другого клиента | encrypted BLOB, hash/materialization, idempotence | реальные заполненные книги NOT_RUN |
| Роли, подписи, PDF и единый черновик | полная регрессия backend/frontend/native | рабочая печать/Excel/пользователи NOT_RUN |
| Геометрия и последние ячейки dev35 | сохранённый native regression набор | фактический OS DPI 125/150/200% NOT_RUN |

Список выше задаёт проверки, но сам по себе не означает PASS. Результат конкретной
сборки определяется отчётами CI, source_commit, release-manifest и сверкой ZIP.
Loopback SMB на Windows Server 2022/2025, два клиента одного аккаунта и fault injection
не заменяют проверки целевой сети, Windows 10/11 и отдельных Windows-пользователей.
Незавершённая проверка обозначается NOT_RUN; падение не скрывается.

Матрицы, пустые значения и подтверждённый ноль, роли и организация заготовок
КГЭ/четырёх дочерних сохраняются. Открытые PIN, приватные ключи, DPAPI-файл и
пользовательская база не должны попасть в дистрибутив.
