# Portable Windows x64

## Проверяемая структура

```text
ReportingSystem/
├─ ReportingSystem.exe
├─ start.cmd
├─ app/
│  ├─ backend/
│  ├─ frontend/
│  └─ migrations/
├─ runtime/
│  ├─ python312.dll, base_library.zip, ...
│  └─ webview2/
├─ config/
├─ resources/
├─ data/
├─ attachments/
├─ imports/inbox/
├─ exports/
├─ backups/
├─ temp/
├─ docs/
├─ VERSION
└─ release-manifest.json
```

`runtime/` является каталогом содержимого PyInstaller `onedir`; это встроенный Python
runtime, а не отдельно устанавливаемый Python. WebView2 Fixed Runtime поставляется в
распакованном виде в `runtime/webview2`. Исходный CAB и установщики в итоговый ZIP не входят.

## Порядок запуска

1. Launcher определяет корень только по расположению `ReportingSystem.exe`, а не по текущему
   каталогу процесса.
2. Проверяет локальный путь, обязательные файлы, выключенные телеметрию, внешнюю сеть и
   технические файловые логи.
3. Создаёт только управляемые пользовательские каталоги и проверяет право записи.
4. Захватывает именованный mutex, уникальный для пути программы, и создаёт `data/app.lock`.
5. Для существующей БД делает согласованную копию SQLite через backup API.
6. Применяет последовательные миграции и проверяет `PRAGMA integrity_check`.
7. Запускает статический frontend через loopback HTTP-сервер PyWebView и прямой PyWebView
   bridge. Backend не слушает внешние интерфейсы.
8. Принудительно выбирает Fixed Runtime из `runtime/webview2`, private-mode и профиль
   `temp/webview2-profile`.
9. При штатном завершении удаляет управляемый WebView2-профиль и lock-файл.

Loopback `127.0.0.1` не является внешним сетевым обращением, но может быть зафиксирован
Windows/EDR. Полностью бесследный запуск не обещается. SQLite journal/WAL, бизнес-аудит,
протокол импорта и резервные копии не являются техническими текстовыми логами и не должны
отключаться ради «чистоты» запуска.

## Сборка

Windows-EXE собирается только на Windows x64: PyInstaller официально не является
кросс-компилятором. Linux-проверка не может заменить запуск на Windows.

```powershell
python -m pip install -r requirements-release.txt
pwsh scripts/build_windows.ps1 `
  -WebView2RuntimePath C:\build-inputs\Microsoft.WebView2.FixedVersionRuntime.x64
```

`requirements-release.txt` фиксирует PyWebView 6.1: именно эта версия добавила штатную
настройку `WEBVIEW2_RUNTIME_PATH`, которую launcher использует для вложенного runtime.

Сценарии останавливаются с ошибкой, если отсутствуют реальный PyInstaller onedir, его
`python3*.dll`/`base_library.zip`, собранный frontend или распакованный x64 Fixed Runtime с
`msedgewebview2.exe`. Скрипты не создают фиктивный Windows executable.

## Скачиваемая тестовая сборка

Workflow `Windows portable preview` собирает отдельный ZIP с суффиксом `-test`, запускает
упакованный `ReportingSystem.exe --self-test` на Windows и публикует результат как GitHub
prerelease, имя которого читается из `VERSION` (для текущей сборки —
`test-v0.1.0-dev.1`). В архив входят launcher, Python runtime, frontend, миграции,
ресурсы и инструкция `TESTING.txt`.

Тестовая сборка использует системный Evergreen WebView2 и предназначена для ручной оценки
интерфейса и SQLite-сохранения. Она не изменяет ACL и не устанавливает компоненты. Полностью
автономная принимаемая поставка по-прежнему должна включать Fixed Runtime после решения
ADR-0001.

## Что проверяется в Linux

- Python unit/integration tests, Ruff и mypy для доступных модулей;
- React/Vite build и статические frontend-тесты после появления frontend;
- создание и проверка `release-manifest.json` на тестовых файлах;
- логика portable-путей, SQLite backup/migration и блокировки;
- статический состав уже полученной Windows-сборки, PE-архитектура, отсутствие `.log` и
  внешних URL в frontend.

В Linux нельзя подтвердить: запуск PE, работу pythonnet/.NET и WebView2, отсутствие UAC,
Windows ACL, поведение Defender/EDR и запуск под обычным Windows-пользователем.

## Обязательная Windows-приёмка

- чистые Windows 10 x64 и Windows 11 x64;
- обычный пользователь, без Python/Node и без администратора;
- распаковка в локальный путь с пробелами и кириллицей;
- `ReportingSystem.exe --self-test`, затем реальный запуск UI;
- повторный запуск блокируется, аварийное завершение восстанавливается;
- backup создаётся и восстанавливается на копии БД;
- приложение использует runtime из ZIP, а не системный Evergreen;
- после сценария отсутствуют собственные `.log`;
- мониторинг процесса не обнаруживает внешние соединения приложения;
- отдельно решён и проверен вопрос Windows 10 ACL из ADR-0001.
