import type { JSX, ReactNode } from "react";

import { ThemeSwitcher } from "./theme-switcher";

type AppShellProps = Readonly<{
  children: ReactNode;
}>;

export function AppShell({ children }: AppShellProps): JSX.Element {
  return (
    <>
      <div className="app-theme-switcher">
        <ThemeSwitcher />
      </div>
      {children}
    </>
  );
}
