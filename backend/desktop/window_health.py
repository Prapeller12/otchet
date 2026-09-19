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
    header && getComputedStyle(header).backgroundColor === 'rgb(17, 26, 34)' &&
    document.querySelector('.status-database')?.textContent.includes('Данные на этом компьютере'));
})()"""


def _wait_for_script(window: Any, expression: str, message: str) -> None:
    deadline = time.monotonic() + 15
    while not window.evaluate_js(expression):
        if time.monotonic() >= deadline:
            error = window.evaluate_js("document.querySelector('[role=alert]')?.textContent || ''")
            raise RuntimeError(f"{message}: {error}" if error else message)
        time.sleep(0.1)


def _click_button(window: Any, label: str) -> None:
    import json

    expression = f"""(() => {{
      const button = [...document.querySelectorAll('button')]
        .find(node => node.textContent.trim() === {json.dumps(label)});
      if (!button || button.disabled) return false;
      button.click(); return true;
    }})()"""
    _wait_for_script(window, expression, f"Недоступна кнопка «{label}»")


def _fill_signing_input(window: Any, label: str, value: str) -> None:
    import json

    window.evaluate_js(f"""(() => {{
      const label = [...document.querySelectorAll('.verification-form label')]
        .find(node => node.textContent.trim() === {json.dumps(label)});
      const input = label?.querySelector('input');
      if (!input || input.disabled) throw new Error('Signing field is not available');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
        .set.call(input, {json.dumps(value)});
      input.dispatchEvent(new Event('input', {{bubbles: true}}));
    }})()""")


def _exercise_signing(window: Any) -> None:
    """Exercise the real React → native bridge → crypto roundtrip in the test copy."""
    _click_button(window, "Подтвердить данные")
    _wait_for_script(
        window,
        "!!document.querySelector('.verification-form input[type=text]:not(:disabled)')",
        "Не открылось создание ключа",
    )
    _fill_signing_input(window, "ФИО пользователя", "Контроль окна")
    _fill_signing_input(window, "Новый PIN (от 6 символов)", "window-test-pin")
    _fill_signing_input(window, "Повтор PIN", "window-test-pin")
    _click_button(window, "Создать ключ пользователя")
    _wait_for_script(
        window,
        "!!document.querySelector('.verification-form input[type=checkbox]:not(:disabled)')",
        "Ключ не создан через окно программы",
    )
    _fill_signing_input(window, "PIN пользователя", "incorrect-test-pin")
    window.evaluate_js("document.querySelector('.verification-form input[type=checkbox]').click()")
    _click_button(window, "Подтверждаю верность данных")
    _wait_for_script(
        window,
        "document.querySelector('.verification-form [role=alert]')?.textContent.includes('PIN')",
        "Неверный PIN не отклонён",
    )
    if not window.evaluate_js(
        "document.querySelector('.verification-form input[type=password]').value === ''"
    ):
        raise RuntimeError("PIN не очищен после попытки подписи")
    _fill_signing_input(window, "PIN пользователя", "window-test-pin")
    _click_button(window, "Подтверждаю верность данных")
    _wait_for_script(
        window,
        "!document.querySelector('.verification-form') && "
        "document.querySelector('.monthly-report-actions [role=status]')"
        "?.textContent.includes('Подтверждено: Контроль окна')",
        "Подпись не подтверждена в окне программы",
    )


def _exercise_matrix_paste(window: Any) -> None:
    """Check navigation, paste preview, guarded unsaved data and native save."""
    window.evaluate_js("""(() => {
      const cell = document.querySelector('.report-matrix tbody button[aria-readonly=false]');
      if (!cell) throw new Error('Editable report cell missing');
      cell.focus(); cell.click();
    })()""")
    _wait_for_script(
        window,
        "document.activeElement?.matches('.report-matrix button[aria-readonly=false]')",
        "Ячейка не получила фокус",
    )
    window.evaluate_js("""document.activeElement.dispatchEvent(
      new KeyboardEvent('keydown', {key:'ArrowRight', bubbles:true, cancelable:true}))""")
    _wait_for_script(
        window,
        "document.activeElement === "
        "document.querySelectorAll('.report-matrix tbody button[aria-readonly=false]')[1]",
        "Не работает переход стрелкой вправо",
    )
    window.evaluate_js("""document.activeElement.dispatchEvent(
      new KeyboardEvent('keydown', {key:'ArrowLeft', bubbles:true, cancelable:true}))""")
    _wait_for_script(
        window,
        "document.activeElement === "
        "document.querySelector('.report-matrix tbody button[aria-readonly=false]')",
        "Не работает переход стрелкой влево",
    )
    window.evaluate_js("""(() => {
      const data = new DataTransfer(); data.setData('text/plain', '17\\t0');
      document.activeElement.dispatchEvent(new ClipboardEvent('paste',
        {clipboardData:data, bubbles:true, cancelable:true}));
    })()""")
    _click_button(window, "Вставить проверенный диапазон")
    _wait_for_script(
        window,
        "[...document.querySelectorAll('.report-tab')].every(button => button.disabled)",
        "Несохранённый диапазон не защищён от переключения вкладки",
    )
    _click_button(window, "Сохранить (2)")
    _wait_for_script(
        window,
        "[...document.querySelectorAll('.report-tab')].every(button => !button.disabled) && "
        "document.querySelector('.monthly-report-actions [role=status]')"
        "?.textContent.includes('Данные изменились')",
        "Сохранение диапазона не обновило состояние подписи",
    )


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
            if ui_self_test and tab == 0:
                _exercise_signing(window)
                _exercise_matrix_paste(window)
            if ui_self_test and tab == 1:
                if not window.evaluate_js("""(() => {
                    const header = document.querySelector('.compact-production-header');
                    const row = header?.querySelector('.compact-production-row');
                    const fields = row
                        ? [...row.querySelectorAll('label, .compact-annual-plan')] : [];
                    return !!header && header.getBoundingClientRect().height <= 60 &&
                        fields.length === 3 &&
                        fields.every(node => Math.abs(node.getBoundingClientRect().top -
                            fields[1].getBoundingClientRect().top) <= 10) &&
                        header.querySelectorAll('input').length === 2 &&
                        !document.querySelector('.production-code-table');
                })()"""):
                    raise RuntimeError("Шапка головной площадки не помещается в одну строку")
            if ui_self_test and tab == 2:
                if not window.evaluate_js("""(() => {
                    const table = document.querySelector('.subsidiary-matrix');
                    return !!table && table.textContent.includes('Обозначение') &&
                        table.textContent.includes('3200') && table.textContent.includes('-500') &&
                        table.textContent.includes('Производитель Б') &&
                        !!document.querySelector('.subsidiary-controls') &&
                        [...table.querySelectorAll('td[data-shared="detail"]')]
                            .filter(c => c.rowSpan === 2).length === 2 &&
                        [...document.querySelectorAll(
                            '.source-auxiliary-matrix td[data-shared="detail"]')]
                            .filter(c => c.rowSpan === 2).length === 1;
                })()"""):
                    raise RuntimeError(
                        "Не отображается недельный отчёт с остатком 3200 и дефицитом -500"
                    )
            if ui_self_test and tab == 2:
                if not window.evaluate_js("""(() => {
                    const button = document.querySelector('.supplier-remove');
                    const headers = [...document.querySelectorAll('.source-header-months th')];
                    const dates = [...document.querySelectorAll('.source-header-dates th')];
                    return button?.getBoundingClientRect().width <= 36 &&
                        headers.length >= 11 && dates.length >= 4 &&
                        headers[0].textContent.includes('Условное изображение') &&
                        headers[1].textContent.includes('№ п/п') &&
                        headers[5].textContent.includes('Производитель') &&
                        headers.slice(6, 10).every(h => h.getBoundingClientRect().width >= 109) &&
                        dates.every(h => h.getBoundingClientRect().width >= 63) &&
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
