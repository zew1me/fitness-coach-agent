import { describe, expect, it } from "vitest";

import { routeTurnIntent } from "../../lib/agent/intent-router";

import { athleteContextFixture } from "./agent-fixtures";

describe("routeTurnIntent", () => {
  it("routes general coaching turns to the lead only", () => {
    expect(routeTurnIntent("Thanks, that helps.", athleteContextFixture)).toMatchObject({
      kind: "general",
      specialists: [],
    });
  });

  it("routes onboarding and profile details to intake", () => {
    expect(
      routeTurnIntent("I can train 6 hours, I run and cycle, and my goal is a July race.", athleteContextFixture)
    ).toMatchObject({
      kind: "intake",
      specialists: ["intake"],
    });
  });

  it("routes fueling and diet turns to nutrition", () => {
    expect(routeTurnIntent("I'm vegetarian and need race carb and sodium advice.", athleteContextFixture)).toMatchObject({
      kind: "nutrition",
      specialists: ["nutrition"],
    });
  });

  it("routes sleep, HRV, soreness, and fatigue turns to recovery", () => {
    expect(routeTurnIntent("My HRV dropped and I slept badly with sore legs.", athleteContextFixture)).toMatchObject({
      kind: "recovery",
      specialists: ["recovery"],
    });
  });

  it("routes completed activities and workout details to workout", () => {
    expect(routeTurnIntent("I ran 8 miles with 4 by 5 minutes at threshold today.", athleteContextFixture)).toMatchObject({
      kind: "workout",
      specialists: ["workout"],
    });
  });

  it("routes plan creation and adjustment turns through recovery before workout", () => {
    expect(routeTurnIntent("Move tomorrow's workout and adjust my plan around the race.", athleteContextFixture)).toMatchObject({
      kind: "plan_change",
      specialists: ["recovery", "workout"],
    });
  });

  it("keeps mixed recovery and workout turns ordered for safe workout decisions", () => {
    expect(routeTurnIntent("I slept badly but still did intervals, should I change the next session?", athleteContextFixture))
      .toMatchObject({
        kind: "mixed",
        specialists: ["recovery", "workout"],
      });
  });
});
