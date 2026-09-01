import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { ApplicationGatewayProvider } from "./app/providers/ApplicationGatewayProvider";
import { createApplicationGateway } from "./shared/api/create-application-gateway";
import "./styles/global.css";

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

void bootstrap();
