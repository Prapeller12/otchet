import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { createServer } from "vite";

// Run with a locally installed Playwright browser; no browser or runtime is shipped.
const { chromium } = createRequire(import.meta.url)("playwright");
const artifacts = path.resolve(process.env.REPORT_LAYOUT_ARTIFACT_DIR ?? "../viewport-review");
await mkdir(artifacts, { recursive: true });
const server = await createServer({ configFile: "vite.config.ts", server: { host: "127.0.0.1", port: 4181, strictPort: true } });
await server.listen();
let browser;
const results = [];
try {
  browser = await chromium.launch({ ...(process.env.REPORT_LAYOUT_BROWSER_EXECUTABLE ? { executablePath: process.env.REPORT_LAYOUT_BROWSER_EXECUTABLE } : {}), args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  const sizes = [{ width: 1920, height: 1080 }, { width: 1366, height: 768 }, { width: 1280, height: 720 }, { width: 1024, height: 640 }, { width: 1024, height: 601 }, { width: 683, height: 384 }, { width: 667, height: 310 }];
  for (const size of sizes) {
    const page = await browser.newPage({ viewport: size });
    const errors = []; page.on("pageerror", error => errors.push(error.message));
    await page.goto("http://127.0.0.1:4181/tests/layout/viewport.html");
    await page.locator(".source-matrix").waitFor(); await page.evaluate(() => document.fonts.ready);
    async function workspace(label, auxiliary = false) {
      // Activating the final primary cell must reveal it below sticky headers,
      // including when only two identity columns remain frozen on a narrow screen.
      const table = auxiliary ? page.locator(".source-auxiliary-matrix") : page.locator(".report-matrix").first();
      const lastCell = table.locator("tbody .matrix-cell-button").last();
      await lastCell.evaluate(button => button.click());
      await page.waitForTimeout(40);
      const geometry = await page.evaluate(auxiliary => {
        const root = document.documentElement;
        const rect = element => { const r = element.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height }; };
        const matrix = document.querySelector(".report-table-region > .matrix-scroll");
        const grid = document.querySelector(auxiliary ? ".source-auxiliary-matrix" : ".report-matrix");
        const frozen = [...grid.querySelectorAll("thead .sticky-left")].filter(x => getComputedStyle(x).position === "sticky" && getComputedStyle(x).left !== "auto");
        const active = grid.querySelector('.matrix-cell-button[tabindex="0"]');
        const stickyHeaders = [...grid.querySelectorAll("thead th")].filter(x => getComputedStyle(x).position === "sticky");
        return { viewport: { width: root.clientWidth, height: root.clientHeight }, document: { width: root.scrollWidth, height: root.scrollHeight }, workspace: rect(document.querySelector(".report-workspace")), matrix: { ...rect(matrix), clientHeight: matrix.clientHeight, scrollWidth: matrix.scrollWidth, scrollHeight: matrix.scrollHeight, scrollTop: matrix.scrollTop }, footer: rect(document.querySelector(".application-status-bar")), rowCount: grid.querySelectorAll("tbody tr").length, frozenRight: Math.max(rect(matrix).left, ...frozen.map(x => rect(x).right)), headerBottom: Math.max(rect(matrix).top, ...stickyHeaders.map(x => rect(x).bottom)), active: active ? rect(active) : null };
      }, auxiliary);
      assert(geometry.document.width <= size.width + 1, `${label}: page horizontal overflow`);
      assert(geometry.document.height <= size.height + 1, `${label}: page vertical overflow`);
      assert(geometry.matrix.right <= size.width + 1 && geometry.matrix.left >= 0, `${label}: matrix outside width`);
      assert(geometry.matrix.bottom <= geometry.footer.top + 1, `${label}: footer overlaps matrix`);
      assert(geometry.matrix.clientHeight >= 96, `${label}: only ${geometry.matrix.clientHeight}px left for matrix`);
      assert.equal(geometry.rowCount, 80, `${label}: required rows changed`);
      assert(geometry.matrix.scrollHeight > geometry.matrix.clientHeight, `${label}: large table has no inner scroll`);
      assert(geometry.frozenRight < geometry.matrix.right - 60, `${label}: frozen identities cover data`);
      assert(geometry.active && Math.min(geometry.active.bottom, geometry.matrix.bottom) - Math.max(geometry.active.top, geometry.headerBottom) >= 20, `${label}: final numeric cell covered by headers`);
      assert(geometry.active.left >= geometry.frozenRight - 1 && geometry.active.right <= geometry.matrix.right + 1, `${label}: final numeric cell covered by frozen identities`);
      if (size.width < 900 && await page.locator(".source-matrix").count() > 0 && !auxiliary) {
        for (const identity of ["photo", "number", "designation", "position", "norm", "party"]) {
          const column = page.locator(`.source-matrix thead [data-identity-column="${identity}"]`).first();
          await column.scrollIntoViewIfNeeded();
          const box = await column.boundingBox();
          assert(box && box.x + box.width > geometry.matrix.left && box.x < geometry.matrix.right, `${label}: original ${identity} column unreachable`);
        }
        await page.locator(".report-matrix").first().locator("tbody .matrix-cell-button").first().evaluate(button => button.click());
        await lastCell.evaluate(button => button.click());
      }
      results.push({ size, label, geometry });
      await page.screenshot({ path: path.join(artifacts, `${size.width}x${size.height}-${label}.png`) });
    }
    async function dialog(label, actions) {
      const modal = page.getByRole("dialog").last(); await modal.waitFor();
      const geometry = await modal.evaluate(element => { const r = element.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, clientWidth: element.clientWidth, scrollWidth: element.scrollWidth }; });
      assert(geometry.left >= -1 && geometry.top >= -1 && geometry.right <= size.width + 1 && geometry.bottom <= size.height + 1, `${label}: dialog outside viewport`);
      assert(geometry.scrollWidth <= geometry.clientWidth + 1, `${label}: dialog body overflows horizontally`);
      for (const action of actions) {
        const button = modal.getByRole("button", { name: action, exact: true }); await button.scrollIntoViewIfNeeded();
        const box = await button.boundingBox(); assert(box && box.y >= -1 && box.y + box.height <= size.height + 1, `${label}: ${action} unreachable`);
        assert(box.height >= 39, `${label}: ${action} too small`);
      }
      results.push({ size, label, geometry }); await page.screenshot({ path: path.join(artifacts, `${size.width}x${size.height}-${label}.png`) });
    }
    await workspace("head");
    await workspace("head-auxiliary", true);
    const firstInput = page.locator(".source-matrix .matrix-cell-button.is-editable").first();
    await firstInput.evaluate(button => button.click());
    await page.locator(".source-matrix").evaluate(table => {
      const clipboard = new DataTransfer();
      clipboard.setData("text/plain", Array.from({ length: 40 }, (_, index) => String(index + 100)).join("\n"));
      table.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: clipboard }));
    });
    await dialog("paste", ["Отмена вставки", "Вставить проверенный диапазон"]);
    await page.getByRole("button", { name: "Вставить проверенный диапазон", exact: true }).click();
    await page.getByRole("button", { name: "Дочерние общества", exact: true }).click();
    await dialog("transition", ["Остаться", "Отбросить и перейти", "Сохранить и перейти"]);
    await page.getByRole("button", { name: "Сохранить и перейти", exact: true }).click();
    await dialog("save-authorization", ["Отмена", "Подтвердить"]);
    await page.getByRole("dialog").last().getByRole("button", { name: "Отмена", exact: true }).click();
    await page.getByRole("button", { name: "Отбросить и перейти", exact: true }).click();
    await page.getByRole("button", { name: "Головная площадка", exact: true }).click();
    await page.locator(".source-matrix th:text-is('Контрагент')").waitFor();
    await page.getByRole("button", { name: "Печать / PDF А4", exact: true }).click(); await dialog("pdf", ["Подтвердить и создать PDF", "Отмена"]);
    await page.getByRole("button", { name: "Подтвердить и создать PDF", exact: true }).click(); await dialog("authorization", ["Отмена", "Подтвердить"]);
    await page.getByRole("dialog").last().getByRole("button", { name: "Отмена", exact: true }).click();
    await page.getByRole("button", { name: "Дочерние общества", exact: true }).click(); await page.locator(".source-matrix:not(:has(th:text-is('Контрагент')))").waitFor(); await workspace("subsidiary");
    await workspace("subsidiary-auxiliary", true);
    await page.getByRole("button", { name: "Ежедневный отчёт", exact: true }).click(); await page.locator(".report-matrix:not(.source-matrix)").waitFor(); await workspace("daily");
    await page.getByRole("button", { name: "Администратор", exact: true }).click();
    await workspace("daily-admin");
    await page.getByRole("button", { name: "Настроить рабочее поле", exact: true }).click(); await dialog("settings", ["Отмена", "Применить настройки"]);
    await page.getByRole("dialog").getByRole("button", { name: "Отмена", exact: true }).click();
    await page.getByRole("button", { name: "Создать заготовки с сентября 2027", exact: true }).click(); await dialog("blank-setup", ["Закрыть заготовки", "Создать или открыть пять заготовок"]);
    await page.getByRole("button", { name: "Закрыть заготовки", exact: true }).click();
    const more = page.getByRole("button", { name: "Ещё", exact: true });
    if (await more.getAttribute("aria-expanded") === "false") await more.click();
    const importButton = page.getByRole("button", { name: "Импорт Excel", exact: true }); await importButton.scrollIntoViewIfNeeded(); await importButton.click(); await dialog("import", ["Закрыть", "Подтвердить импорт"]);
    await page.getByRole("dialog").getByRole("button", { name: "Закрыть", exact: true }).click();
    for (const wide of ["position", "photo"]) {
      await page.goto(`http://127.0.0.1:4181/tests/layout/viewport.html?wide=${wide}`);
      await page.locator(".source-matrix").waitFor(); await page.evaluate(() => document.fonts.ready);
      await workspace(`head-wide-${wide}`);
      await workspace(`head-wide-${wide}-auxiliary`, true);
      await page.getByRole("button", { name: "Дочерние общества", exact: true }).click();
      await page.locator(".source-matrix:not(:has(th:text-is('Контрагент')))").waitFor();
      await workspace(`subsidiary-wide-${wide}`);
      await workspace(`subsidiary-wide-${wide}-auxiliary`, true);
    }
    await page.goto("http://127.0.0.1:4181/tests/layout/viewport.html?access=setup");
    await page.getByRole("heading", { name: "Первый запуск", exact: true }).waitFor();
    for (const name of ["Создать администратора и начать", "Восстановить из резервной копии"]) {
      const button = page.getByRole("button", { name, exact: true }); await button.scrollIntoViewIfNeeded();
      const box = await button.boundingBox(); assert(box && box.y >= 0 && box.y + box.height <= size.height + 1 && box.height >= 39, `first-launch: ${name} unreachable`);
    }
    const accessGeometry = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight, cardHeight: document.querySelector(".access-card").clientHeight }));
    assert(accessGeometry.width <= size.width + 1 && accessGeometry.height <= size.height + 1, "first-launch: page overflow");
    results.push({ size, label: "first-launch", geometry: accessGeometry }); await page.screenshot({ path: path.join(artifacts, `${size.width}x${size.height}-first-launch.png`) });
    await page.goto("http://127.0.0.1:4181/tests/layout/viewport.html?access=notice");
    await page.locator(".source-matrix").waitFor(); await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.locator(".workspace-context .access-startup-notice").count(), 1, "startup notice outside bounded context");
    await workspace("head-startup-notice");
    assert.deepEqual(errors, [], `browser errors at ${size.width}x${size.height}`);
    console.log(`PASS viewport ${size.width}x${size.height}`);
    await page.close();
  }
  await writeFile(path.join(artifacts, "geometry.json"), JSON.stringify({ browser: browser.version(), results }, null, 2));
  console.log(`PASS: ${results.length} real-browser viewport/dialog checks; ${artifacts}`);
} finally {
  await writeFile(path.join(artifacts, "geometry.json"), JSON.stringify({ browser: browser?.version(), results }, null, 2));
  await browser?.close(); await server.close();
}
