import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";
import type { Metadata } from "next";
import type { JSX, ReactNode } from "react";

import "./globals.css";

import { AppShell } from "../components/app-shell";
import { siteConfig } from "../lib/site";
import { themeInitializer } from "../lib/theme-initializer";

export const metadata: Metadata = {
  title: siteConfig.appName,
  description: siteConfig.description,
  icons: {
    icon: "/brand/peak-mark-horizon.svg",
  },
};

type RootLayoutProps = Readonly<{
  children: ReactNode;
}>;

export default function RootLayout({ children }: RootLayoutProps): JSX.Element {
  return (
    <html
      data-theme="light"
      data-theme-mode="system"
      lang="en"
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitializer }} />
      </head>
      <body>
        <AppShell>{children}</AppShell>
        <Analytics />
        <SpeedInsights />
      </body>
    </html>
  );
}
