# Backend tests

Тесты добавляются одновременно с соответствующей реализацией. Обязательный набор определён
разделом 18 `AGENTS.md`.

- `unit/test_calculations.py` — точные доменные формулы и граничные состояния;
- `unit/test_repository_verifier.py` — защита состава репозитория;
- `integration/test_sqlite_migrations.py` — мигратор, ограничения, транзакции и сторно.
