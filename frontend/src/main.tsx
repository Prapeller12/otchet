import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { ApplicationGatewayProvider } from "./app/providers/ApplicationGatewayProvider";
import { createApplicationGateway } from "./shared/api/create-application-gateway";
import "./styles/global.css";
import "./styles/design-tokens.css";
import "./styles/kanban-layout.css";

async function bootstrap(): Promise<void> {
  const rootElement = document.getElementById("root");
  if (rootElement === null) throw new Error("Root element was not found");

  const gateway = await createApplicationGateway();
  createRoot(rootElement).render(
    <StrictMode>
      <ApplicationGatewayProvider gateway={gateway}>
        <App />
      </ApplicationGatewayProvider>
    </StrictMode>,
  );
}

void bootstrap().catch((error: unknown) => {
  const root = document.getElementById("root");
  if (root) {
    const message = document.createElement("p");
    message.setAttribute("role", "alert");
    message.style.cssText = "padding:24px;font-family:Arial,sans-serif";
    message.textContent = `Не удалось запустить интерфейс: ${error instanceof Error ? error.message : String(error)}. Закройте программу и распакуйте полный ZIP в новую локальную папку.`;
    root.replaceChildren(message);
  }
});
