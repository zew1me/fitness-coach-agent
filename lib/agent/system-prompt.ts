import type { AthleteContextBundle, GoalContext } from "./types";

function listOrFallback(values: string[], fallback: string): string {
  return values.length > 0 ? values.join(", ") : fallback;
}

function goalSummary(goal: GoalContext): string {
  const course =
    goal.course_distance_meters !== null && goal.course_distance_meters !== undefined
      ? `, course ${Math.round(goal.course_distance_meters)}m`
      : "";
  const gain =
    goal.course_elevation_gain_meters !== null && goal.course_elevation_gain_meters !== undefined
      ? `, gain ${Math.round(goal.course_elevation_gain_meters)}m`
      : "";
  const target = goal.target_date ? `, target ${goal.target_date}` : "";
  return `${goal.title} (${goal.goal_type}${target}${course}${gain})`;
}

function stateInstructions(state: string): string {
  if (state === "onboarding") {
    return [
      "State: onboarding.",
      "Be conversational and extract multiple fields from each user turn.",
      "Minimum to advance: at least one sport, at least one goal, and a fitness signal.",
      "Collect age or birth date early so CTL guidance is realistic.",
    ].join(" ");
  }

  if (state === "calibrating") {
    return [
      "State: calibrating.",
      "Ask for recent workouts, screenshots, GPX/FIT uploads, or race/test results.",
      "Use tools to estimate thresholds and establish CTL before prescribing a full plan.",
    ].join(" ");
  }

  if (state === "paused") {
    return [
      "State: paused.",
      "Help the athlete re-enter with reduced load and conservative recovery assumptions.",
    ].join(" ");
  }

  return [
    "State: active.",
    "Coach the ongoing loop: log work, monitor compliance, update recovery, recalibrate, and adjust.",
  ].join(" ");
}

export function buildCoachSystemPrompt(context: AthleteContextBundle): string {
  const sports = listOrFallback(context.profile.primary_sports, "unknown");
  const goals = context.goals.map(goalSummary).join("; ") || "none recorded";
  const load = context.current_load
    ? `CTL ${context.current_load.ctl}, ATL ${context.current_load.atl}, TSB ${context.current_load.tsb}`
    : "no current load snapshot";
  const age = context.computed_age === null ? "unknown" : String(context.computed_age);

  return [
    "You are a sport-agnostic endurance coach.",
    "Use Seiler-informed training principles: mostly Z1-Z2 volume, carefully dosed intensity, recovery weeks, and honest but encouraging nudges.",
    "Be inclusive and ask about sex or hormone context only when it improves training-load guidance.",
    `Athlete: ${context.profile.display_name ?? context.profile.user_id}. Age: ${age}. Sports: ${sports}.`,
    `Goals: ${goals}.`,
    `Current load: ${load}.`,
    `CTL ceiling guidance: ${context.ctl_ceiling_guidance.age_bracket}; committed amateur CTL ${context.ctl_ceiling_guidance.committed_amateur_ctl}; ${context.ctl_ceiling_guidance.notes}`,
    stateInstructions(context.profile.coaching_state),
    "After 3-4 consistent weeks at a sustainable frequency, suggest a small progression if the athlete's goals warrant it.",
    "Use tools for persistence and deterministic calculations. Do not invent metrics that are missing.",
  ].join("\n\n");
}
