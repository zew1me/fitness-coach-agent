import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../../components/coach-chat.module.css", import.meta.url), "utf8");

function ruleBody(selector: string): string {
  const escapedSelector = selector.replaceAll(".", "\\.");
  const match = new RegExp(`${escapedSelector}\\s*\\{([^}]*)\\}`, "m").exec(css);
  if (match === null) {
    throw new Error(`Missing CSS rule for ${selector}`);
  }

  return match[1] ?? "";
}

describe("coach chat contrast styles", () => {
  it("sets explicit readable colors for assistant messages and metadata", () => {
    expect(ruleBody(".assistantBubble")).toContain("background: var(--coach-bubble)");
    expect(ruleBody(".assistantBubble")).toContain("color: var(--text)");
    expect(ruleBody(".assistantBubble .messageText")).toContain("color: var(--text)");
    expect(ruleBody(".assistantBubble .attachmentName")).toContain("color: var(--text-muted)");
    expect(ruleBody(".userBubble")).toContain("color: #ffffff");
  });
});
