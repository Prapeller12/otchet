import { afterEach, expect, it, vi } from "vitest";
import { createApplicationGateway } from "../../src/shared/api/create-application-gateway";
const bridge = vi.hoisted(() => ({ present: false, gateway: { mode: "pywebview" } }));
vi.mock("../../src/shared/api/pywebview-gateway", () => ({
  hasPyWebViewBridge: () => bridge.present,
  PyWebViewGateway: { fromWindow: () => bridge.gateway },
}));
afterEach(() => { vi.useRealTimers(); vi.unstubAllEnvs(); bridge.present = false; });
it("waits for a late desktop bridge instead of showing demo", async () => {
  vi.stubEnv("DEV", false);
  vi.useFakeTimers();
  const pending = createApplicationGateway();
  await vi.advanceTimersByTimeAsync(1000);
  bridge.present = true;
  window.dispatchEvent(new Event("pywebviewready"));
  expect(await pending).toBe(bridge.gateway);
});
it("fails visibly when the desktop bridge never starts", async () => {
  vi.stubEnv("DEV", false);
  vi.useFakeTimers();
  const pending = expect(createApplicationGateway()).rejects.toThrow("Нет связи");
  await vi.advanceTimersByTimeAsync(15_000);
  await pending;
});
