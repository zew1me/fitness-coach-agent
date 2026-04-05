import Link from "next/link";
import type { JSX, ReactNode } from "react";

import { siteConfig } from "../lib/site";

import { BrandMark } from "./brand-mark";
import { ThemeSwitcher } from "./theme-switcher";

type AppShellProps = Readonly<{
  children: ReactNode;
}>;

export function AppShell({ children }: AppShellProps): JSX.Element {
  return (
    <div className="app-shell">
      <header className="app-header">
        <Link className="brand-lockup" href="/">
          <BrandMark />
          <div className="brand-title">
            <strong>{siteConfig.appName}</strong>
            <span>Adaptive coaching for endurance athletes</span>
          </div>
        </Link>
        <div className="header-controls">
          <nav className="app-nav">
            <Link href="/">Dashboard</Link>
            <Link href="/profile">Profile</Link>
            <Link href="/login?return_to=/">Login</Link>
          </nav>
          <ThemeSwitcher />
        </div>
      </header>
      {children}
    </div>
  );
}
