import type { ReactNode } from "react";

import { ChatTurnLeaseProvider } from "./chat-turn-lease-provider";
import { QueryProvider } from "./query-provider";

type AppShellProps = Readonly<{
  children: ReactNode;
}>;

export function AppShell({ children }: AppShellProps): ReactNode {
  return (
    <QueryProvider>
      <ChatTurnLeaseProvider>{children}</ChatTurnLeaseProvider>
    </QueryProvider>
  );
}
