import { describe, expect, it } from "vitest";

import {
  workoutVisualFamily,
  WORKOUT_FAMILY_LEGEND,
} from "../../lib/calendar-workout-visuals";

describe("calendar workout visual families", () => {
  it.each([
    ["recovery", "recovery"],
    ["rest", "recovery"],
    ["mobility", "recovery"],
    ["endurance", "endurance"],
    ["long_run", "endurance"],
    ["long_ride", "endurance"],
    ["tempo", "tempo"],
    ["sweet_spot", "tempo"],
    ["threshold", "threshold"],
    ["hill_repeats", "threshold"],
    ["vo2max", "high_intensity"],
    ["anaerobic", "high_intensity"],
    ["sprint", "high_intensity"],
    ["race", "high_intensity"],
    ["interval", "high_intensity"],
    ["fartlek", "high_intensity"],
    ["brick", "high_intensity"],
    ["strength", "strength"],
  ])("maps %s to the %s family", (workoutType, expectedFamily) => {
    expect(workoutVisualFamily(workoutType)).toBe(expectedFamily);
  });

  it("uses a neutral fallback for future workout types", () => {
    expect(workoutVisualFamily("future_workout_type")).toBe("other");
  });

  it("provides a concise legend for every intentional color family", () => {
    expect(WORKOUT_FAMILY_LEGEND.map(({ family }) => family)).toEqual([
      "recovery",
      "endurance",
      "tempo",
      "threshold",
      "high_intensity",
      "strength",
    ]);
  });
});
