# Сценарии

Проверка базовой структуры:

```bash
python scripts/verify_repository.py
```

Portable-контур:

- `build_windows.ps1` — запускается только на Windows x64, проверяет backend/frontend и
  создаёт PyInstaller `onedir`;
- `package_portable.ps1` — собирает раздельную ZIP-структуру только из реального launcher,
  встроенного Python, собранного frontend и распакованного WebView2 Fixed Runtime;
- `verify_release.ps1` / `verify_release.py` — fail-closed проверка состава, PE x64,
  offline-настроек, manifest и отсутствия внешних URL в frontend;
- `release_manifest.py` — контрольные суммы неизменяемых файлов поставки.

Сценарии не скачивают runtime на целевом компьютере и не создают фиктивный EXE. Полная
приёмка требует чистых Windows 10/11; ограничения и команды описаны в
`docs/release/portable-windows.md`.
