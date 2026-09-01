import type { ApplicationGateway } from "./application-gateway";
import { DemoGateway } from "./demo-gateway";
import {
  hasPyWebViewBridge,
  PyWebViewGateway,
} from "./pywebview-gateway";

const BRIDGE_WAIT_MS = 250;

export async function createApplicationGateway(): Promise<ApplicationGateway> {
  if (hasPyWebViewBridge()) return PyWebViewGateway.fromWindow();

  await new Promise<void>((resolve) => {
    const timeout = window.setTimeout(resolve, BRIDGE_WAIT_MS);
    window.addEventListener(
      "pywebviewready",
      () => {
        window.clearTimeout(timeout);
        resolve();
      },
      { once: true },
    );
  });

  return hasPyWebViewBridge()
    ? PyWebViewGateway.fromWindow()
    : new DemoGateway();
}
