import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { createServer } from "vite";

const { chromium } = createRequire(import.meta.url)("playwright");
const artifacts = path.resolve(process.env.REPORT_LAYOUT_ARTIFACT_DIR ?? "../network-viewport-review");
await mkdir(artifacts, { recursive: true });
const server = await createServer({ configFile: "vite.config.ts", server: { host: "127.0.0.1", port: 4182, strictPort: true } });
await server.listen();
let browser;
const results = [];
try {
  browser = await chromium.launch({ ...(process.env.REPORT_LAYOUT_BROWSER_EXECUTABLE ? { executablePath: process.env.REPORT_LAYOUT_BROWSER_EXECUTABLE } : {}), args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  for (const size of [{ width: 1366, height: 768 }, { width: 1024, height: 640 }, { width: 683, height: 384 }, { width: 667, height: 310 }]) {
    const page = await browser.newPage({ viewport: size });
    const errors = []; page.on("pageerror", error => errors.push(error.message));
    await page.goto("http://127.0.0.1:4182/tests/layout/viewport.html?network=1");
    await page.locator(".source-matrix").waitFor(); await page.evaluate(() => document.fonts.ready);
    async function record(label) {
      await page.locator(".source-matrix tbody .matrix-cell-button").last().evaluate(button => button.click());
      await page.waitForTimeout(40);
      const geometry = await page.evaluate(() => {
        const rect = element => { const r = element.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height }; };
        return { viewport: { width: innerWidth, height: innerHeight }, document: { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight }, context: rect(document.querySelector(".workspace-context")), network: rect(document.querySelector(".network-status")), matrix: { ...rect(document.querySelector(".report-table-region > .matrix-scroll")), clientHeight: document.querySelector(".report-table-region > .matrix-scroll").clientHeight }, footer: rect(document.querySelector(".application-status-bar")), rows: document.querySelectorAll(".source-matrix tbody tr").length };
      });
      assert(geometry.document.width <= size.width + 1 && geometry.document.height <= size.height + 1, `${label}: global page overflow`);
      assert(geometry.matrix.right <= size.width + 1 && geometry.matrix.bottom <= geometry.footer.top + 1 && geometry.matrix.clientHeight >= 96, `${label}: matrix escapes work area`);
      assert.equal(geometry.rows, 80, `${label}: required rows changed`);
      for (const button of await page.locator(".network-actions button").all()) {
        await button.scrollIntoViewIfNeeded(); const box = await button.boundingBox();
        assert(box && box.y >= geometry.context.top - 1 && box.y + box.height <= geometry.context.bottom + 1 && box.x >= 0 && box.x + box.width <= size.width + 1 && box.height >= 39, `${label}: network action inaccessible`);
      }
      results.push({ size, label, geometry }); await page.screenshot({ path: path.join(artifacts, `${size.width}x${size.height}-${label}.png`) });
    }
    await record("network-ready");
    for (const state of ["stale", "unavailable", "outcome_unknown"]) {
      await page.evaluate(state => window.dispatchEvent(new CustomEvent("network-status-changed", { detail: { mode: "network", state, loaded_global_revision: "synthetic:g1", current_global_revision: "synthetic:g2", snapshot_changed: true, can_write: false, message: "Синтетический длинный текст состояния общего хранилища: открытый снимок не заменяется проверкой соединения, введённые значения остаются в форме. ".repeat(8), pending_operation_id: state === "outcome_unknown" ? "synthetic-operation" : null } })), state);
      await record(`network-${state}`);
    }
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("network-status-changed", { detail: { mode: "network", state: "ready", loaded_global_revision: "synthetic:g1", current_global_revision: "synthetic:g1", snapshot_changed: false, can_write: true, message: null, pending_operation_id: null } })));
    await page.getByRole("button", { name: "Обновить общие данные", exact: true }).click();
    await page.locator(".network-status [role=alert]").waitFor(); await record("network-refresh-error");
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("network-activation-warning", { detail: "Подтверждённая публикация уже находится в общем хранилище; локальная копия требует явного обновления. ".repeat(8) })));
    await record("network-activation-notice");
    assert.deepEqual(errors, [], `${size.width}x${size.height}: browser errors`);
    console.log(`PASS full App network context ${size.width}x${size.height}`); await page.close();
  }
  console.log(`PASS ${results.length} full App network viewport checks`);
} finally {
  await writeFile(path.join(artifacts, "geometry.json"), JSON.stringify({ browser: browser?.version(), results }, null, 2));
  await browser?.close(); await server.close();
}
