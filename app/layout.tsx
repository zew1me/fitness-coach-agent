import type { Metadata } from "next";
import type { JSX, ReactNode } from "react";

import { AppShell } from "../components/app-shell";
import { siteConfig } from "../lib/site";

export const metadata: Metadata = {
  title: siteConfig.appName,
  description: siteConfig.description
};

type RootLayoutProps = Readonly<{
  children: ReactNode;
}>;

export default function RootLayout({ children }: RootLayoutProps): JSX.Element {
  return (
    <html lang="en">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
