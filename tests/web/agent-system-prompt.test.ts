import { describe, expect, it } from "vitest";

import { buildCoachSystemPrompt, buildLeadCoachPrompt, buildSpecialistPrompt } from "../../lib/agent/system-prompt";
import type { AthleteContextBundle } from "../../lib/agent/types";

const context: AthleteContextBundle = {
  profile: {
    user_id: "athlete-1",
    display_name: "Sam",
    primary_sports: ["running", "cycling"],
    coaching_state: "onboarding",
    weekly_available_hours: 6
  },
  computed_age: 39,
  thresholds: [],
  goals: [
    {
      id: "goal-1",
      user_id: "athlete-1",
      goal_type: "event",
      sport: "running",
      title: "14km hill climb",
      target_date: "2026-07-01",
      course_distance_meters: 14000,
      course_elevation_gain_meters: 700,
      priority: 1,
      status: "active"
    }
  ],
  current_load: {
    user_id: "athlete-1",
    snapshot_date: "2026-04-01",
    daily_tss: 60,
    ctl: 42,
    atl: 50,
    tsb: -8
  },
  recent_recovery: [],
  schedule: null,
  active_plan: null,
  ctl_ceiling_guidance: {
    age_bracket: "30-39",
    elite_ctl: 130,
    committed_amateur_ctl: 85,
    recreational_ctl: 45,
    recovery_week_frequency: "every 3-4 weeks",
    notes: "Recovery starts to matter more."
  }
};

describe("buildCoachSystemPrompt", () => {
  it("includes coaching philosophy, athlete context, and onboarding instructions", () => {
    const prompt = buildCoachSystemPrompt(context);

    expect(prompt).toContain("Seiler");
    expect(prompt).toContain("onboarding");
    expect(prompt).toContain("14km hill climb");
    expect(prompt).toContain("CTL 42");
    expect(prompt).toContain("30-39");
    expect(prompt).toContain("Onboarding goal");
    expect(prompt).toContain("sport context");
    expect(prompt).toContain("coaching objective");
    expect(prompt).toContain("one or two natural follow-up questions");
    expect(prompt).toContain("Nutrition should wait until after the opening exchange");
    // Age-specific balance guidance must not appear for under-65 athletes.
    expect(prompt).not.toContain("balance and fall-prevention");
  });

  it("includes current-date guidance and requires a response after tool use", () => {
    const prompt = buildCoachSystemPrompt(context);

    expect(prompt).toContain(`Current date: ${new Date().toISOString().slice(0, 10)}`);
    expect(prompt).toContain("Do not guess the current date");
    expect(prompt).toContain("After any tool call");
    expect(prompt).toContain("user-facing response");
  });

  it("includes both training models and age-specific balance note for classification by the LLM", () => {
    const longevityContext: AthleteContextBundle = {
      ...context,
      computed_age: 67,
      profile: {
        ...context.profile,
        coaching_state: "active",
        primary_sports: ["walking", "strength"]
      },
      goals: [
        {
          id: "goal-longevity",
          user_id: "athlete-1",
          goal_type: "maintenance",
          sport: "general",
          title: "Longevity and aging well",
          priority: 1,
          status: "active"
        }
      ]
    };

    const prompt = buildCoachSystemPrompt(longevityContext);

    // Both models are always present — the LLM classifies from goal context.
    expect(prompt).toContain("Longevity/health model");
    expect(prompt).toContain("150–300 min/week");
    expect(prompt).toContain("2× strength/week");
    expect(prompt).toContain("VO2max maintenance");
    expect(prompt).toContain("Performance/Seiler model");
    // Age-based balance note is still injected server-side for 65+ athletes.
    expect(prompt).toContain("balance and fall-prevention work");
    // LLM is instructed to classify rather than having it pre-decided.
    expect(prompt).toContain("read the athlete's goals and profile, then choose");
    // Ambiguous goals must prompt clarification, not a silent default.
    expect(prompt).toContain("mixed or ambiguous");
    expect(prompt).toContain("ask the athlete which matters more");
  });

  it("includes balance note at exactly age 65", () => {
    const prompt = buildCoachSystemPrompt({ ...context, computed_age: 65 });

    expect(prompt).toContain("balance and fall-prevention work");
  });

  it("omits balance note when computed_age is null", () => {
    const prompt = buildCoachSystemPrompt({ ...context, computed_age: null });

    expect(prompt).not.toContain("balance and fall-prevention");
    expect(prompt).toContain("Longevity/health model");
  });

  it("includes nutrition context when dietary_restrictions are set", () => {
    const nutritionContext: AthleteContextBundle = {
      ...context,
      profile: {
        ...context.profile,
        dietary_restrictions: ["vegetarian", "lactose intolerant"],
        nutrition_notes: "Prefers gels over real food during races"
      }
    };
    const prompt = buildCoachSystemPrompt(nutritionContext);

    expect(prompt).toContain("vegetarian");
    expect(prompt).toContain("lactose intolerant");
    expect(prompt).toContain("Prefers gels over real food");
  });

  it("omits nutrition context lines when fields are absent", () => {
    const prompt = buildCoachSystemPrompt(context);

    expect(prompt).not.toContain("Dietary restrictions:");
    expect(prompt).not.toContain("Nutrition notes:");
    expect(prompt).not.toContain("Athlete nutrition context:");
  });

  it("includes nutrition principles for female athletes without explicit nutrition context", () => {
    const femaleContext: AthleteContextBundle = {
      ...context,
      profile: { ...context.profile, biological_sex: "female" }
    };
    const prompt = buildCoachSystemPrompt(femaleContext);

    expect(prompt).toContain("LEA");
    expect(prompt).toContain("Fuel the work");
  });

  it("does not include nutrition principles for male athletes without nutrition context", () => {
    const maleContext: AthleteContextBundle = {
      ...context,
      profile: { ...context.profile, biological_sex: "male" }
    };
    const prompt = buildCoachSystemPrompt(maleContext);

    expect(prompt).not.toContain("LEA");
  });

  it("builds role-specific specialist prompts that forbid user-facing prose and persistence", () => {
    const prompt = buildSpecialistPrompt("recovery", { recent_recovery: context.recent_recovery });

    expect(prompt).toContain("Recovery specialist");
    expect(prompt).toContain("structured report only");
    expect(prompt).toContain("Do not write user-facing prose");
    expect(prompt).toContain("Do not call tools or persist data");
  });

  it("builds a lead coach prompt that includes specialist reports and final-response guidance", () => {
    const prompt = buildLeadCoachPrompt(context, [
      {
        confidence: "medium",
        proposedUpdates: [],
        risks: ["Keep intensity conservative."],
        role: "workout",
        summary: "Workout specialist suggests moving intervals after poor sleep.",
      },
    ]);

    expect(prompt).toContain("Lead coach");
    expect(prompt).toContain("Workout specialist suggests moving intervals");
    expect(prompt).toContain("Keep intensity conservative");
    expect(prompt).toContain("user-facing response");
  });
});
