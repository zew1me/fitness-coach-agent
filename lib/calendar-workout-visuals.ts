export const WORKOUT_FAMILY_LEGEND = [
  { family: "recovery", label: "Recovery" },
  { family: "endurance", label: "Endurance" },
  { family: "tempo", label: "Tempo / sweet spot" },
  { family: "threshold", label: "Threshold" },
  { family: "high_intensity", label: "VO₂ / anaerobic / race" },
  { family: "strength", label: "Strength" },
] as const;

export type WorkoutVisualFamily =
  | (typeof WORKOUT_FAMILY_LEGEND)[number]["family"]
  | "other";

const WORKOUT_TYPE_FAMILIES: Readonly<Record<string, WorkoutVisualFamily>> = {
  recovery: "recovery",
  rest: "recovery",
  mobility: "recovery",
  endurance: "endurance",
  long_run: "endurance",
  long_ride: "endurance",
  tempo: "tempo",
  sweet_spot: "tempo",
  threshold: "threshold",
  hill_repeats: "threshold",
  vo2max: "high_intensity",
  anaerobic: "high_intensity",
  sprint: "high_intensity",
  race: "high_intensity",
  interval: "high_intensity",
  fartlek: "high_intensity",
  brick: "high_intensity",
  strength: "strength",
};

/**
 * Collapse the plan schema's detailed workout types into a small visual
 * vocabulary. The source workout type remains unchanged and visible in the
 * detail panel; this family is presentation-only.
 */
export function workoutVisualFamily(workoutType: string): WorkoutVisualFamily {
  return WORKOUT_TYPE_FAMILIES[workoutType] ?? "other";
}
