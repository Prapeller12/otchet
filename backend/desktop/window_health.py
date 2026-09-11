"""Bounded startup health check of the actual embedded browser, not just Python."""

from __future__ import annotations

import importlib
import time
from typing import Any

from backend.desktop.paths import PortablePaths

_READY = """(() => {
  const table = document.querySelector('.report-matrix');
  const header = document.querySelector('.app-header');
  return !!(window.pywebview && window.pywebview.api && table &&
    table.querySelector('tbody tr') && table.getBoundingClientRect().width > 500 &&
    header && getComputedStyle(header).backgroundColor === 'rgb(36, 41, 47)' &&
    document.querySelector('.status-database')?.textContent.includes('SQLite'));
})()"""


def monitor_window(
    window: Any, paths: PortablePaths, *, ui_self_test: bool, failures: list[str]
) -> None:
    """Fail visibly if the document, CSS, bridge or first report never loads."""
    try:
        if not window.events.loaded.wait(45):
            raise RuntimeError("WebView2 не загрузил страницу за 45 секунд")
        for tab in range(3 if ui_self_test else 1):
            if tab:
                window.evaluate_js(f"document.querySelectorAll('.report-tab')[{tab}].click()")
                time.sleep(0.5)
            deadline = time.monotonic() + 30
            while not window.evaluate_js(_READY):
                error = window.evaluate_js(
                    "document.querySelector('[role=alert]')?.textContent || ''"
                )
                if error:
                    raise RuntimeError(str(error))
                if time.monotonic() >= deadline:
                    raise RuntimeError("Не загрузились таблица, оформление или связь с базой")
                time.sleep(0.25)
            if ui_self_test and tab == 2:
                if not window.evaluate_js("""(() => {
                    const table = document.querySelector('.subsidiary-matrix');
                    return !!table && table.textContent.includes('Обозначение') &&
                        table.textContent.includes('3200') && table.textContent.includes('-500') &&
                        table.textContent.includes('Производитель Б') &&
                        !!document.querySelector('.subsidiary-controls') &&
                        [...table.querySelectorAll('td[data-shared="detail"]')]
                            .filter(c => c.rowSpan === 2).length === 3;
                })()"""):
                    raise RuntimeError(
                        "Не отображается недельный отчёт с остатком 3200 и дефицитом -500"
                    )
            if ui_self_test and tab == 2:
                if not window.evaluate_js("""(() => {
                    const button = document.querySelector('.supplier-remove');
                    const headers = [...document.querySelectorAll('.matrix-header-leaf-row th')];
                    return button?.getBoundingClientRect().width <= 36 &&
                        headers.slice(0, 4).every(h => h.getBoundingClientRect().width >= 109) &&
                        !document.querySelector('.subsidiary-controls')
                            .textContent.includes('C6') &&
                        !!document.querySelector('.week-heading[aria-pressed="true"]');
                })()"""):
                    raise RuntimeError("Неверные размеры или подписи дочернего отчёта")
                window.evaluate_js("document.querySelector('.header-settings-button').click()")
                deadline = time.monotonic() + 10
                while not window.evaluate_js(
                    "!!document.querySelector('.subsidiary-detail-editor')"
                ):
                    if time.monotonic() > deadline:
                        raise RuntimeError("Настройки детали не открылись")
                    time.sleep(0.25)
                if not window.evaluate_js("""(() => {
                    const editor = document.querySelector('.subsidiary-detail-editor');
                    const rows = [...editor.querySelectorAll('.subsidiary-supplier')];
                    return getComputedStyle(editor).display === 'block' &&
                        rows[1].getBoundingClientRect().top >=
                            rows[0].getBoundingClientRect().bottom;
                })()"""):
                    raise RuntimeError("Производители в настройках не выровнены")
                importlib.import_module("PIL.ImageGrab").grab().save(
                    paths.temp / "window-settings.png"
                )
                window.evaluate_js("document.querySelector('.settings-header button').click()")
            if ui_self_test:
                grab = importlib.import_module("PIL.ImageGrab")
                grab.grab().save(paths.temp / f"window-{tab + 1}.png")
        if ui_self_test:
            window.evaluate_js("""(() => {
              const select = document.querySelector('.reference-selector select');
              if (!select || select.options.length < 2)
                throw new Error('Imported workbook not listed');
              select.value = select.options[1].value;
              select.dispatchEvent(new Event('change', {bubbles:true}));
            })()""")
            deadline = time.monotonic() + 30
            while not window.evaluate_js("!!document.querySelector('.reference-grid')"):
                if time.monotonic() > deadline:
                    raise RuntimeError("Импортированный отчёт не открылся")
                time.sleep(0.25)
            if not window.evaluate_js(
                "document.querySelector('.reference-grid').textContent.includes('675')"
            ):
                raise RuntimeError("Не отображается результат формулы импортированного отчёта")
            grab = importlib.import_module("PIL.ImageGrab")
            grab.grab().save(paths.temp / "window-4.png")
            window.destroy()
    except Exception as exc:
        failures.append(
            f"Ошибка загрузки окна: {exc}. Распакуйте полный ZIP в новую локальную папку. "
            "В комплекте должен быть каталог runtime/webview2."
        )
        window.destroy()
