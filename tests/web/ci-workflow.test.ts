import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// Regression test for #185: every push to a branch with an open PR fired
// both a `push`-triggered run and a `pull_request`-triggered run of the same
// CI workflow, doubling every job (including the ~20min Playwright UI job).
describe("CI workflow triggers", () => {
  const workflow = readFileSync(
    join(process.cwd(), ".github/workflows/ci.yml"),
    "utf-8",
  );

  it("does not trigger on push for arbitrary branches (only main)", () => {
    // A bare `push:` trigger (no branch filter) fires for every branch,
    // including branches that already have an open PR driving a
    // pull_request-triggered run of the same workflow.
    expect(workflow).not.toMatch(/^on:\n\s*push:\n\s*pull_request:/m);
    expect(workflow).toMatch(/push:\n\s*branches:\n\s*- main/);
  });

  it("cancels superseded runs via a concurrency group", () => {
    expect(workflow).toMatch(
      /concurrency:\n\s*group:.*\n\s*cancel-in-progress: true/,
    );
  });
});
