import Link from "next/link";
import type { JSX, ReactNode } from "react";

import { siteConfig } from "../lib/site";

type AppShellProps = Readonly<{
  children: ReactNode;
}>;

export function AppShell({ children }: AppShellProps): JSX.Element {
  return (
    <div
      style={{
        background:
          "radial-gradient(circle at top, rgba(14, 165, 233, 0.14), transparent 32%), linear-gradient(180deg, #fff7ed 0%, #f8fafc 48%, #ecfeff 100%)",
        margin: "0 auto",
        maxWidth: "1120px",
        minHeight: "100vh",
        padding: "1.5rem"
      }}
    >
      <header
        style={{
          alignItems: "center",
          display: "flex",
          gap: "1rem",
          justifyContent: "space-between",
          marginBottom: "2rem"
        }}
      >
        <strong>{siteConfig.appName}</strong>
        <nav style={{ display: "flex", gap: "1rem" }}>
          <Link href="/">Dashboard</Link>
          <Link href="/profile">Profile</Link>
          <Link href="/login?return_to=/">Login</Link>
        </nav>
      </header>
      {children}
    </div>
  );
}
