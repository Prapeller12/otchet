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
    header && getComputedStyle(document.body).fontFamily.includes('Golos Text') &&
    getComputedStyle(document.documentElement).getPropertyValue('--color-accent').trim()
      === '#e5402b' &&
    document.querySelector('.status-database')?.textContent.includes('Данные на этом компьютере'));
})()"""


def _wait_for_script(window: Any, expression: str, message: str) -> None:
    deadline = time.monotonic() + 15
    while not window.evaluate_js(expression):
        if time.monotonic() >= deadline:
            error = window.evaluate_js("document.querySelector('[role=alert]')?.textContent || ''")
            raise RuntimeError(f"{message}: {error}" if error else message)
        time.sleep(0.1)


def _settle_window_paint(window: Any) -> None:
    """Wait for fonts, two render frames and the native compositor before capture."""
    window.evaluate_js("""(() => {
      window.__reportCapturePaintReady = false;
      document.fonts.ready.then(() => requestAnimationFrame(() =>
        requestAnimationFrame(() => { window.__reportCapturePaintReady = true; })));
      return true;
    })()""")
    _wait_for_script(
        window,
        "window.__reportCapturePaintReady === true",
        "Шрифты или отрисовка окна не завершились",
    )
    # ImageGrab captures the OS compositor, which can lag WebView's DOM/rAF.
    time.sleep(0.25)


def _capture_window(window: Any, paths: PortablePaths, filename: str) -> None:
    _settle_window_paint(window)
    importlib.import_module("PIL.ImageGrab").grab().save(paths.temp / filename)


def _resize_test_window(window: Any, *, compact: bool) -> None:
    window.resize(1024 if compact else 1440, 768 if compact else 900)
    width_range = "innerWidth >= 980 && innerWidth <= 1024" if compact else "innerWidth > 1200"
    _wait_for_script(window, width_range, "Не изменился размер проверочного окна")
    _settle_window_paint(window)


def _check_reference_theme(window: Any, *, controls: bool = True) -> None:
    _wait_for_script(
        window,
        """(() => {
          const body = getComputedStyle(document.body);
          const faces = [...document.fonts].filter(face => face.family.replace(/["']/g, '')
            === 'Golos Text');
          return body.fontFamily.includes('Golos Text') &&
            body.backgroundColor === 'rgb(243, 244, 247)' &&
            faces.some(face => face.status === 'loaded') &&
            document.fonts.check('14px "Golos Text"', 'Отчёт 0123456789');
        })()""",
        "Локальный Golos Text или светлая тема образца не загрузились",
    )
    if controls and not window.evaluate_js("""(() => {
      const buttons = [...document.querySelectorAll('.matrix-toolbar button')];
      return buttons.length === 3 && buttons.every(button => {
        const style = getComputedStyle(button);
        return button.getBoundingClientRect().height >= 40 &&
          parseFloat(style.borderTopLeftRadius) === 10 &&
          style.fontFamily.includes('Golos Text');
      });
    })()"""):
        raise RuntimeError("Основные кнопки не соответствуют размерам и шрифту образца")


def _check_action_icons(window: Any) -> None:
    """Check useful, painted SVGs and accessible names, not just icon imports."""
    if not window.evaluate_js("""(() => {
      const buttons = [...document.querySelectorAll('.matrix-toolbar button')];
      const names = ['save', 'print', 'chevron-down'];
      return buttons.length === 3 && buttons.every((button, index) => {
        const icon = button.querySelector('svg[data-icon="' + names[index] + '"]');
        const box = icon?.getBoundingClientRect();
        return icon && box.width >= 12 && box.height >= 12 &&
          getComputedStyle(icon).visibility === 'visible' &&
          getComputedStyle(icon).display !== 'none' &&
          icon.getAttribute('aria-hidden') === 'true' &&
          icon.getAttribute('focusable') === 'false' && !!icon.querySelector('path');
      });
    })()"""):
        raise RuntimeError("У сохранения, печати или «Ещё» отсутствует видимая SVG-иконка")
    _click_button(window, "Ещё")
    _wait_for_script(
        window,
        """!!document.querySelector('.matrix-toolbar button[aria-expanded=true] '
          + 'svg[data-icon="chevron-up"]') &&
          document.querySelector('#report-more-actions')?.hidden === false""",
        "Иконка «Ещё» не показывает раскрытое состояние",
    )
    _click_button(window, "Ещё")
    _wait_for_script(
        window,
        """!!document.querySelector('.matrix-toolbar button[aria-expanded=false] '
          + 'svg[data-icon="chevron-down"]') &&
          document.querySelector('#report-more-actions')?.hidden === true""",
        "Меню «Ещё» не закрывается со сменой иконки",
    )
    _check_icon_accessibility(window)


def _check_icon_accessibility(window: Any) -> None:
    if not window.evaluate_js("""(() => {
      const buttons = [...document.querySelectorAll('button')]
        .filter(button => button.getClientRects().length && button.querySelector('svg'));
      const previousFocus = document.activeElement;
      const accessible = buttons.every(button => {
        const text = button.textContent.trim();
        const label = button.getAttribute('aria-label') || text ||
          (button.getAttribute('aria-labelledby') || '').split(/\\s+/)
            .map(id => document.getElementById(id)?.textContent || '').join('').trim();
        if (!text && !button.disabled) {
          if (button.tabIndex < 0) return false;
          button.focus({preventScroll: true});
          if (document.activeElement !== button) return false;
        }
        return !!label && [...button.querySelectorAll('svg')].every(icon =>
          icon.getAttribute('aria-hidden') === 'true' &&
          icon.getAttribute('focusable') === 'false');
      });
      previousFocus?.focus({preventScroll: true});
      return accessible;
    })()"""):
        raise RuntimeError("У кнопки с иконкой отсутствует доступное имя или SVG мешает фокусу")


def _capture_report_viewports(window: Any, paths: PortablePaths, tab: int) -> None:
    _check_reference_theme(window)
    _check_action_icons(window)
    _capture_window(window, paths, f"window-{tab + 1}.png")
    _resize_test_window(window, compact=True)
    _check_reference_theme(window)
    if not window.evaluate_js("""document.documentElement.scrollWidth <= innerWidth + 1 &&
        [...document.querySelectorAll('.matrix-toolbar button')].every(button => {
          const box = button.getBoundingClientRect();
          return box.left >= 0 && box.right <= innerWidth && box.bottom <= innerHeight;
        })"""):
        raise RuntimeError("При ширине 1024 кнопки отчёта или страница выходят за окно")
    if tab == 1 and not window.evaluate_js("""(() => {
      const header = document.querySelector('.readonly-production-header');
      const fields = header ? [...header.children] : [];
      return !!header && header.getBoundingClientRect().height <= 60 && fields.length === 3 &&
        fields.every(node => Math.abs(node.getBoundingClientRect().top -
          fields[0].getBoundingClientRect().top) <= 10);
    })()"""):
        raise RuntimeError("Шапка головной площадки не помещается в одну строку при ширине 1024")
    _capture_window(window, paths, f"window-{tab + 1}-compact.png")
    _resize_test_window(window, compact=False)


def _click_button(window: Any, label: str) -> None:
    import json

    expression = f"""(() => {{
      const button = [...document.querySelectorAll('button')]
        .find(node => node.textContent.trim() === {json.dumps(label)} &&
          !node.disabled && node.getClientRects().length > 0 &&
          getComputedStyle(node).visibility === 'visible');
      if (!button) return false;
      button.click(); return true;
    }})()"""
    _wait_for_script(window, expression, f"Недоступна кнопка «{label}»")


def _fill_form_input(window: Any, scope: str, label: str, value: str) -> None:
    import json

    # The access page first renders its shell, then waits for the native status.
    # Readiness and input happen in one evaluation so a rerender cannot race them.
    expression = f"""(() => {{
      const label = [...document.querySelectorAll({json.dumps(scope + " label")})]
        .find(node => node.textContent.trim() === {json.dumps(label)});
      const input = label?.querySelector('input');
      if (!input || input.disabled || input.readOnly || !input.getClientRects().length ||
          getComputedStyle(input).visibility !== 'visible') return false;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
        .set.call(input, {json.dumps(value)});
      input.dispatchEvent(new Event('input', {{bubbles: true}}));
      return true;
    }})()"""
    _wait_for_script(window, expression, f"Недоступно поле «{label}» ({scope})")


def _unlock_test_window(window: Any, paths: PortablePaths) -> None:
    """Use the same locked-screen path that a person uses after restarting."""
    _wait_for_script(window, "!!document.querySelector('.access-page')", "Не открыт вход")
    _wait_for_script(
        window,
        "!!document.querySelector('.access-page input[type=password]')",
        "Нет поля кода доступа",
    )
    _resize_test_window(window, compact=True)
    _check_reference_theme(window, controls=False)
    _capture_window(window, paths, "window-access-compact.png")
    _resize_test_window(window, compact=False)
    _fill_form_input(window, ".access-page", "Код доступа", "window-test-pin")
    _click_button(window, "Открыть отчёты")
    _wait_for_script(
        window, "!!document.querySelector('.onboarding-dialog')", "Нет подсказок заполнения"
    )
    _click_button(window, "Далее")
    _wait_for_script(
        window,
        "document.querySelector('#onboarding-title')?.textContent === 'Заполните таблицу'",
        "Нет второго шага подсказок",
    )
    _click_button(window, "Далее")
    _click_button(window, "Начать заполнение")
    _wait_for_script(window, _READY, "После входа не открылась рабочая форма")
    if not window.evaluate_js("""(() => {
      const buttons = [...document.querySelectorAll('.matrix-toolbar button')];
      return buttons.length === 3 && buttons[0].textContent.includes('Сохранить') &&
        buttons[1].textContent === 'Печать / PDF А4' && buttons[2].textContent === 'Ещё' &&
        document.querySelector('#report-more-actions')?.hidden &&
        !document.querySelector('.admin-navigation');
    })()"""):
        raise RuntimeError("Основное поле заполнения перегружено действиями администратора")


def _confirm_write(
    window: Any,
    *,
    reject_wrong_code: bool = False,
    action: str = "Подтвердить",
    project_manager: bool = False,
    capture_paths: PortablePaths | None = None,
) -> None:
    _wait_for_script(
        window, "!!document.querySelector('.authorization-dialog')", "Нет запроса кода записи"
    )
    if capture_paths is not None:
        _wait_for_script(
            window,
            "document.querySelector('.authorization-dialog select')?.options.length > 0",
            "В подтверждении не загрузились ответственные лица",
        )
        if not window.evaluate_js("""(() => {
          const box = document.querySelector('.authorization-dialog').getBoundingClientRect();
          return box.top >= 0 && box.left >= 0 && box.bottom <= innerHeight &&
            box.right <= innerWidth;
        })()"""):
            raise RuntimeError("Окно подтверждения не помещается при ширине 1024")
        _check_icon_accessibility(window)
        _capture_window(window, capture_paths, "window-confirmation-compact.png")
    if project_manager:
        _wait_for_script(
            window,
            """(() => {
          const select = document.querySelector('.authorization-dialog select');
          const option = [...(select?.options ?? [])]
            .find(node => node.textContent.includes('Руководитель проверки окна'));
          if (!option) return false;
          select.value = option.value;
          select.dispatchEvent(new Event('change', {bubbles:true})); return true;
        })()""",
            "Руководителю проекта недоступно подтверждение своего отчёта",
        )
    if reject_wrong_code:
        _fill_form_input(window, ".authorization-dialog", "Код подтверждения", "wrong-code")
        _click_button(window, action)
        _wait_for_script(
            window,
            "!!document.querySelector('.authorization-dialog [role=alert]')",
            "Неверный код не отклонён",
        )
        if not window.evaluate_js(
            "document.querySelector('.authorization-dialog input[type=password]').value === ''"
        ):
            raise RuntimeError("Код не очищен после неудачного подтверждения записи")
    pin = "project-window-pin" if project_manager else "window-test-pin"
    _fill_form_input(window, ".authorization-dialog", "Код подтверждения", pin)
    _click_button(window, action)
    _wait_for_script(
        window, "!document.querySelector('.authorization-dialog')", "Запись не подтверждена"
    )


def _exercise_responsible_person(window: Any) -> None:
    """An authenticated administrator creates a person without a second PIN prompt."""
    _click_button(window, "Администратор")
    _confirm_write(window)
    _click_button(window, "Ответственные лица")
    _fill_form_input(window, ".admin-users", "Имя ответственного", "Руководитель проверки окна")
    _fill_form_input(window, ".admin-users", "Личный код (от 6 символов)", "project-window-pin")
    _fill_form_input(window, ".admin-users", "Повтор личного кода", "project-window-pin")
    window.evaluate_js("""(() => {
      const select = document.querySelector('.admin-users form select');
      select.value = 'project_manager';
      select.dispatchEvent(new Event('change', {bubbles:true}));
    })()""")
    _click_button(window, "Создать ключ ответственного")
    _wait_for_script(
        window,
        "document.querySelector('.admin-users [role=status]')?.textContent"
        ".includes('Ключ создан: Руководитель проверки окна')",
        "Создание ответственного требует повторного кода или завершилось ошибкой",
    )
    if window.evaluate_js("!!document.querySelector('.authorization-dialog')"):
        raise RuntimeError("Создание ответственного повторно запрашивает код администратора")
    _click_button(window, "К заполнению отчётов")
    _wait_for_script(
        window,
        _READY + " && !document.querySelector('.admin-navigation')",
        "Не восстановлен экран заполнения",
    )


def _exercise_print(window: Any, paths: PortablePaths) -> None:
    """Print asks for a code, signs the actual report and produces a real PDF."""
    if window.evaluate_js("""[...document.querySelectorAll('button')]
        .some(button => button.textContent.trim() === 'Подтвердить данные')"""):
        raise RuntimeError("Осталась отдельная кнопка подтверждения данных")
    _resize_test_window(window, compact=True)
    _click_button(window, "Печать / PDF А4")
    _confirm_write(
        window, action="Подтвердить и печатать", reject_wrong_code=True, capture_paths=paths
    )
    _resize_test_window(window, compact=False)
    _wait_for_script(
        window,
        "document.querySelector('.monthly-report-actions [role=status]')?.textContent"
        ".includes('Подтверждено: Контроль окна') && "
        "document.body.textContent.includes('PDF сохранён:')",
        "Печать не подтвердила отчёт или не создала PDF",
    )
    pdfs = list(paths.temp.glob("*.pdf"))
    if not pdfs or not all(path.read_bytes().startswith(b"%PDF-") for path in pdfs):
        raise RuntimeError("Печать не создала настоящий PDF в проверочной папке")


def _exercise_daily_columns_and_navigation(window: Any) -> None:
    if not window.evaluate_js("""(() => {
      const headers = [...document.querySelectorAll('.report-matrix thead th')];
      const totals = headers.filter(node => node.textContent.trim() === 'Накопительный итог');
      return totals.length === 1 &&
        !headers.some(node => node.textContent.trim() === 'С начала года') &&
        document.querySelectorAll(
          '.report-matrix tbody tr:first-child .daily-summary-value').length === 1;
    })()"""):
        raise RuntimeError("В ежедневном отчёте должен быть один накопительный итог")
    window.evaluate_js("window.scrollTo(0, document.documentElement.scrollHeight)")
    _wait_for_script(
        window,
        """(() => {
      const nav = document.querySelector('.workspace-navigation');
      const box = nav?.getBoundingClientRect();
      return window.scrollY > 0 && box && box.top >= -1 && box.top <= 1 &&
        box.bottom < innerHeight && [...nav.querySelectorAll('.report-tab')]
          .every(button => button.getBoundingClientRect().top >= 0);
    })()""",
        "Переключатель отчётов уходит за верхнюю границу при прокрутке",
    )
    window.evaluate_js("window.scrollTo(0, 0)")
    _wait_for_script(window, "window.scrollY === 0", "Не восстановлено начало отчёта")


def _exercise_project_header(window: Any) -> None:
    _click_button(window, "План и сведения")
    _fill_form_input(window, ".production-header", "Наименование изделия", "Изделие проверки окна")
    _fill_form_input(window, ".production-header", "Шифр изделия", "ПРОВЕРКА-27")
    _click_button(window, "Сохранить")
    _confirm_write(window, action="Сохранить и подтвердить", project_manager=True)
    _wait_for_script(
        window,
        "document.querySelector('.monthly-report-actions [role=status]')?.textContent"
        ".includes('Подтверждено: Руководитель проверки окна')",
        "Руководитель проекта не сохранил и не подтвердил сведения",
    )
    _click_button(window, "К заполнению отчётов")
    _wait_for_script(
        window,
        "document.querySelector('.readonly-production-header')?.textContent"
        ".includes('Изделие проверки окна')",
        "Изменения руководителя не отображаются в отчёте",
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
    _confirm_write(
        window, action="Сохранить и подтвердить", reject_wrong_code=True, project_manager=True
    )
    _wait_for_script(
        window,
        "[...document.querySelectorAll('.report-tab')].every(button => !button.disabled) && "
        "document.querySelector('.monthly-report-actions [role=status]')"
        "?.textContent.includes('Подтверждено: Руководитель проверки окна')",
        "Сохранение диапазона не подписало новые данные кодом руководителя",
    )


def monitor_window(
    window: Any, paths: PortablePaths, *, ui_self_test: bool, failures: list[str]
) -> None:
    """Fail visibly if the document, CSS, bridge or first report never loads."""
    try:
        if not window.events.loaded.wait(45):
            raise RuntimeError("WebView2 не загрузил страницу за 45 секунд")
        if ui_self_test:
            _unlock_test_window(window, paths)
        for tab in range(3 if ui_self_test else 1):
            if tab:
                window.evaluate_js(f"document.querySelectorAll('.report-tab')[{tab}].click()")
                time.sleep(0.5)
            deadline = time.monotonic() + 30
            # The access screen is a healthy normal startup. Do not time out while
            # a person reads the instructions or looks up their code.
            ready = (
                _READY
                if ui_self_test
                else (
                    "!!(window.pywebview?.api && document.querySelector('.access-page')) || "
                    + _READY
                )
            )
            while not window.evaluate_js(ready):
                error = window.evaluate_js(
                    "document.querySelector('[role=alert]')?.textContent || ''"
                )
                if error:
                    raise RuntimeError(str(error))
                if time.monotonic() >= deadline:
                    raise RuntimeError("Не загрузились таблица, оформление или связь с базой")
                time.sleep(0.25)
            if ui_self_test and tab == 0:
                _exercise_responsible_person(window)
                _exercise_print(window, paths)
                _exercise_matrix_paste(window)
                _exercise_daily_columns_and_navigation(window)
            if ui_self_test and tab == 1:
                _capture_window(window, paths, "window-head-header.png")
                if not window.evaluate_js("""(() => {
                    const header = document.querySelector('.readonly-production-header');
                    const fields = header ? [...header.children] : [];
                    return !!header && header.getBoundingClientRect().height <= 60 &&
                        fields.length === 3 &&
                        fields.every(node => Math.abs(node.getBoundingClientRect().top -
                            fields[1].getBoundingClientRect().top) <= 10) &&
                        header.querySelectorAll('input').length === 0 &&
                        !document.querySelector('.production-code-table');
                })()"""):
                    geometry = window.evaluate_js("""JSON.stringify(
                        [...document.querySelectorAll('.readonly-production-header, '
                            + '.readonly-production-header > *')].map(node => ({
                                tag: node.tagName,
                                height: node.getBoundingClientRect().height,
                                top: node.getBoundingClientRect().top
                            })))""")
                    raise RuntimeError(
                        f"Шапка головной площадки не помещается в одну строку: {geometry}"
                    )
                _exercise_project_header(window)
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
                    return !button &&
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
                _click_button(window, "Администратор")
                _confirm_write(window)
                _click_button(window, "Настроить рабочее поле")
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
                _check_icon_accessibility(window)
                _capture_window(window, paths, "window-settings.png")
                _resize_test_window(window, compact=True)
                _capture_window(window, paths, "window-settings-compact.png")
                _resize_test_window(window, compact=False)
                window.evaluate_js("document.querySelector('.settings-header button').click()")
                _click_button(window, "К заполнению отчётов")
                _wait_for_script(
                    window,
                    _READY + " && !document.querySelector('.admin-navigation')",
                    "Не восстановлен экран заполнения",
                )
            if ui_self_test:
                _capture_report_viewports(window, paths, tab)
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
            _check_icon_accessibility(window)
            _capture_window(window, paths, "window-4.png")
            window.destroy()
    except Exception as exc:
        if ui_self_test:
            try:
                importlib.import_module("PIL.ImageGrab").grab().save(
                    paths.temp / "window-error.png"
                )
            except Exception:
                pass  # Preserve the original failure if capture is unavailable.
        failures.append(
            f"Ошибка загрузки окна: {exc}. Распакуйте полный ZIP в новую локальную папку. "
            "В комплекте должен быть каталог runtime/webview2."
        )
        window.destroy()
