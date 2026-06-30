import type { JSX, ReactNode } from "react";

import { QueryProvider } from "./query-provider";

type AppShellProps = Readonly<{
  children: ReactNode;
}>;

export function AppShell({ children }: AppShellProps): JSX.Element {
  return <QueryProvider>{children}</QueryProvider>;
}
