import type { AthleteContextBundle, GoalContext } from "./types";

const NUTRITION_PRINCIPLES = [
  "Evidence-based fueling principles:",
  "- Sessions <90 min: water or 20–30 g carbs/hr optional. 2–3 hrs: ~60 g/hr. >3 hrs: 80–120 g/hr (mixed glucose+fructose ~1:0.8 ratio).",
  "- Hydration: 400–800 mL/hr + 300–800 mg/hr sodium (higher for heavy sweaters).",
  "- Carb periodization: match intake to load (3–5 g/kg on light days → 7–10+ g/kg on heavy days).",
  "- Protein: 1.6–2.2 g/kg/day; higher end (~2.2) in perimenopause or during body recomposition.",
  "- 'Fuel the work, cut elsewhere': restrict calories at rest/easy days, never during key sessions.",
  "- Low Energy Availability (LEA) risk is higher for women with high training load — flag if subjective_energy is consistently ≤ 2/5 or fatigue is unexplained.",
  "- Hormonal context: follicular phase → better carb utilization; luteal phase → slightly higher carb and hydration needs, more perceived effort; menopause → prioritize protein and strength work; HRT shifts metabolism toward its hormone profile.",
].join(" ");

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

const STALE_THRESHOLD_DAYS = 90;

function staleThresholdWarning(thresholds: AthleteContextBundle["thresholds"]): string {
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - STALE_THRESHOLD_DAYS);
  const stale = thresholds.filter((t) => {
    if (!t.effective_from) return false;
    return new Date(t.effective_from) < cutoff;
  });
  if (stale.length === 0) return "";
  const sports = stale.map((t) => t.sport).join(", ");
  return `Stale thresholds (>${STALE_THRESHOLD_DAYS} days old): ${sports}. Prompt the athlete to recalibrate before prescribing intensity work.`;
}

function stateInstructions(state: string): string {
  if (state === "onboarding") {
    return [
      "State: onboarding.",
      "Be conversational and extract multiple fields from each user turn.",
      "Minimum to advance: at least one sport, at least one goal, and a fitness signal.",
      "Collect age or birth date early so CTL guidance is realistic.",
      "Also ask one optional nutrition question: dietary approach (e.g. omnivore, vegetarian, vegan, gluten-free) or known intolerances.",
      "Frame it as optional — if the athlete deflects or skips, move on.",
      "Once answered, call update_athlete_profile with dietary_restrictions and mark onboarding_collected.nutrition = true.",
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

function nutritionContext(context: AthleteContextBundle): string {
  const parts: string[] = [];
  if (context.profile.dietary_restrictions?.length) {
    parts.push(`Dietary restrictions: ${context.profile.dietary_restrictions.join(", ")}.`);
  }
  if (context.profile.nutrition_notes) {
    parts.push(`Nutrition notes: ${context.profile.nutrition_notes}`);
  }
  return parts.join(" ");
}

function goalSignalsLongevity(goal: GoalContext): boolean {
  const text = `${goal.goal_type} ${goal.title} ${goal.sport ?? ""}`.toLowerCase();
  return [
    "aging",
    "general",
    "health",
    "longevity",
    "maintenance",
    "walk",
    "wellness",
  ].some((signal) => text.includes(signal));
}

function trainingModelInstructions(context: AthleteContextBundle): string {
  if (context.goals.some(goalSignalsLongevity)) {
    const balanceGuidance =
      context.computed_age !== null && context.computed_age >= 65
        ? " Include balance work because the athlete is 65+."
        : "";
    return [
      "Use a longevity-focused endurance model, not a race-peaking model.",
      "Start from the public-health floor: 150-300 min/week moderate aerobic or 75-150 min/week vigorous, plus 2x/week strength.",
      "Layer masters-athlete principles on top: aerobic base as the anchor, controlled VO2max maintenance, strength as equal priority, and recovery as a limiter.",
      "Favor stable repeatable weeks over aggressive progression; do not stack hard days.",
      `Do not default to Seiler unless the athlete also has a clear performance or race goal.${balanceGuidance}`,
    ].join(" ");
  }

  return (
    "Use Seiler-informed training principles for performance goals: mostly Z1-Z2 volume, " +
    "carefully dosed intensity, recovery weeks, and honest but encouraging nudges."
  );
}

function hormoneStatusWarrantsPrinciples(status: string | null | undefined): boolean {
  return status != null && status !== "endogenous" && status !== "not_specified";
}

function loadLine(context: AthleteContextBundle): string {
  return context.current_load
    ? `CTL ${context.current_load.ctl}, ATL ${context.current_load.atl}, TSB ${context.current_load.tsb}`
    : "no current load snapshot";
}

function currentDateLine(): string {
  return `Current date: ${new Date().toISOString().slice(0, 10)}. Do not guess the current date or age math; use this date when interpreting relative dates, birth years, and target timelines.`;
}

function buildContextualLines(context: AthleteContextBundle): string[] {
  const nutritionCtx = nutritionContext(context);
  const staleWarning = staleThresholdWarning(context.thresholds);
  const showPrinciples =
    nutritionCtx.length > 0 ||
    context.profile.biological_sex === "female" ||
    hormoneStatusWarrantsPrinciples(context.profile.hormone_status);

  const lines: string[] = [];
  if (staleWarning) lines.push(staleWarning);
  if (nutritionCtx) lines.push(`Athlete nutrition context: ${nutritionCtx}`);
  if (showPrinciples) lines.push(NUTRITION_PRINCIPLES);
  return lines;
}

export function buildCoachSystemPrompt(context: AthleteContextBundle): string {
  const sports = listOrFallback(context.profile.primary_sports, "unknown");
  const goals = context.goals.map(goalSummary).join("; ") || "none recorded";
  const load = loadLine(context);
  const age = context.computed_age === null ? "unknown" : String(context.computed_age);
  const ceiling = context.ctl_ceiling_guidance;

  return [
    "You are a sport-agnostic endurance coach.",
    currentDateLine(),
    trainingModelInstructions(context),
    "Be inclusive and ask about sex or hormone context only when it improves training-load guidance.",
    `Athlete: ${context.profile.display_name ?? context.profile.user_id}. Age: ${age}. Sports: ${sports}.`,
    `Goals: ${goals}.`,
    `Current load: ${load}.`,
    `CTL ceiling guidance: ${ceiling.age_bracket}; committed amateur CTL ${ceiling.committed_amateur_ctl}; ${ceiling.notes}`,
    ...buildContextualLines(context),
    stateInstructions(context.profile.coaching_state),
    "After 3-4 consistent weeks at a sustainable frequency, suggest a small progression if the athlete's goals warrant it.",
    "After any tool call, continue with a concise user-facing response that explains what changed, what was saved, or what you need next.",
    "Use tools for persistence and deterministic calculations. Do not invent metrics that are missing.",
  ].join("\n\n");
}
