import type { JSX, ReactNode } from "react";

import { siteConfig } from "../lib/site";

type AppShellProps = Readonly<{
  children: ReactNode;
}>;

export function AppShell({ children }: AppShellProps): JSX.Element {
  return (
    <div style={{ margin: "0 auto", maxWidth: "960px", padding: "2rem" }}>
      <header style={{ marginBottom: "2rem" }}>
        <strong>{siteConfig.appName}</strong>
      </header>
      {children}
    </div>
  );
}
