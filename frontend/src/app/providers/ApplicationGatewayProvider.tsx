import {
  createContext,
  useContext,
  type PropsWithChildren,
} from "react";

import type { ApplicationGateway } from "../../shared/api/application-gateway";

const ApplicationGatewayContext = createContext<ApplicationGateway | null>(null);

type ApplicationGatewayProviderProps = PropsWithChildren<{
  gateway: ApplicationGateway;
}>;

export function ApplicationGatewayProvider({
  gateway,
  children,
}: ApplicationGatewayProviderProps) {
  return (
    <ApplicationGatewayContext.Provider value={gateway}>
      {children}
    </ApplicationGatewayContext.Provider>
  );
}
export function useApplicationGateway(): ApplicationGateway {
  const gateway = useContext(ApplicationGatewayContext);
  if (gateway === null) {
    throw new Error("ApplicationGatewayProvider is missing");
  }
  return gateway;
}
