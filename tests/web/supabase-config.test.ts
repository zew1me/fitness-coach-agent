import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

describe("supabase auth config", () => {
  const config = readFileSync(new URL("../../supabase/config.toml", import.meta.url), "utf8");

  function section(name: string): string {
    const match = config.match(new RegExp(`\\[${name.replace(".", "\\.")}\\]\\n([\\s\\S]*?)(?=\\n\\[|$)`));
    return match?.[1] ?? "";
  }

  it("disables direct email signups so invite creation must go through the app route", () => {
    expect(section("auth")).toMatch(/^enable_signup = false$/m);
    expect(section("auth.email")).toMatch(/^enable_signup = false$/m);
  });
});
