import type { InternalSpecialistRole, TurnIntent } from "./orchestration-types";
import type { AthleteContextBundle } from "./types";

function includesAny(text: string, patterns: RegExp[]): boolean {
  return patterns.some((pattern) => pattern.test(text));
}

function orderedSpecialists(roles: InternalSpecialistRole[]): InternalSpecialistRole[] {
  const order: InternalSpecialistRole[] = ["intake", "nutrition", "recovery", "workout"];
  const unique = new Set(roles);
  return order.filter((role) => unique.has(role));
}

type IntentSignals = {
  intake: boolean;
  nutrition: boolean;
  plan: boolean;
  recovery: boolean;
  workout: boolean;
};

function detectSignals(text: string): IntentSignals {
  const intake = includesAny(text, [
    /\b(age|born|birth|goal|sport|schedule|available|availability)\b/,
    /\btrain(?:ing)?\s+\d+\s*(hours|hrs|h)\b/,
    /\b(run|running|cycle|cycling|ride|swim|walking)\b/,
  ]);
  const nutrition = includesAny(text, [
    /\b(carb|carbs|fuel|fueling|hydration|sodium|protein|gel|gels|diet|vegetarian|vegan|gluten|lactose)\b/,
  ]);
  const recovery = includesAny(text, [
    /\b(sleep|slept|hrv|sore|soreness|fatigue|tired|resting hr|body battery|stress|sick|ill)\b/,
  ]);
  const workout = includesAny(text, [
    /\b(workout|session|activity|ran|rode|cycled|swam|miles|mile|km|interval|intervals|threshold|tempo|long run)\b/,
  ]);
  const planSubjectSignal = includesAny(text, [/\b(plan|tomorrow|calendar)\b/]);
  const planActionSignal = includesAny(text, [/\b(adjust|move|create|generate|rebuild|reschedule)\b/]);
  return {
    intake,
    nutrition,
    plan: planSubjectSignal && planActionSignal,
    recovery,
    workout,
  };
}

function specialistsForSignals(
  signals: IntentSignals,
  context: AthleteContextBundle
): InternalSpecialistRole[] {
  const roles: InternalSpecialistRole[] = [];
  if (signals.intake && !signals.nutrition && context.profile.coaching_state === "onboarding") {
    roles.push("intake");
  }
  if (signals.nutrition) roles.push("nutrition");
  if (signals.recovery) roles.push("recovery");
  if (signals.workout) roles.push("workout");
  return orderedSpecialists(roles);
}

function intentFromSpecialists(specialists: InternalSpecialistRole[]): TurnIntent {
  if (specialists.length === 0) return { kind: "general", specialists };
  if (specialists.length > 1) return { kind: "mixed", specialists };
  return { kind: specialists[0] ?? "general", specialists };
}

export function routeTurnIntent(latestUserText: string, context: AthleteContextBundle): TurnIntent {
  const signals = detectSignals(latestUserText.toLowerCase());

  if (signals.plan && signals.workout) {
    return {
      kind: "plan_change",
      specialists: orderedSpecialists(["recovery", "workout"]),
    };
  }

  if (signals.recovery && signals.workout) {
    return { kind: "mixed", specialists: orderedSpecialists(["recovery", "workout"]) };
  }

  return intentFromSpecialists(specialistsForSignals(signals, context));
}
