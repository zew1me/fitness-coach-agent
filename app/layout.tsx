import { SpeedInsights } from "@vercel/speed-insights/next";
import type { Metadata } from "next";
import type { JSX, ReactNode } from "react";

import "./globals.css";

import { AppShell } from "../components/app-shell";
import { siteConfig } from "../lib/site";

export const metadata: Metadata = {
  title: siteConfig.appName,
  description: siteConfig.description,
  icons: {
    icon: "/brand/peak-mark-horizon.svg"
  }
};

type RootLayoutProps = Readonly<{
  children: ReactNode;
}>;

const themeInitializer = `
(() => {
  const storageKey = "fitness-theme-preference";
  const saved = window.localStorage.getItem(storageKey);
  const mode = saved === "light" || saved === "dark" || saved === "system" ? saved : "system";
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const resolved = mode === "system" ? (systemDark ? "dark" : "light") : mode;
  document.documentElement.dataset.themeMode = mode;
  document.documentElement.dataset.theme = resolved;
})();
`;

export default function RootLayout({ children }: RootLayoutProps): JSX.Element {
  return (
    <html data-theme="light" data-theme-mode="system" lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitializer }} />
      </head>
      <body>
        <AppShell>{children}</AppShell>
        <SpeedInsights />
      </body>
    </html>
  );
}
