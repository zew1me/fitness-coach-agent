import { describe, expect, it } from "vitest";

import { siteConfig } from "../../lib/site";

describe("siteConfig", () => {
  it("exposes the app name", () => {
    expect(siteConfig.appName).toContain("Exercise Training Plan GPT");
  });
});
