import type { ApplicationGateway } from "./application-gateway";
import { DemoGateway } from "./demo-gateway";
import {
  hasPyWebViewBridge,
  PyWebViewGateway,
} from "./pywebview-gateway";

const BRIDGE_WAIT_MS = 15_000;

export async function createApplicationGateway(): Promise<ApplicationGateway> {
  if (hasPyWebViewBridge()) return PyWebViewGateway.fromWindow();
  // Demo is an explicit development mode, never a silent desktop fallback.
  if (import.meta.env.DEV) return new DemoGateway();

  await new Promise<void>((resolve, reject) => {
    const ready = () => {
      window.clearTimeout(timeout);
      window.removeEventListener("pywebviewready", ready);
      resolve();
    };
    const timeout = window.setTimeout(() => {
      window.removeEventListener("pywebviewready", ready);
      reject(new Error("Нет связи с локальной базой программы"));
    }, BRIDGE_WAIT_MS);
    window.addEventListener("pywebviewready", ready);
    if (hasPyWebViewBridge()) ready();
  });
  return PyWebViewGateway.fromWindow();
}
