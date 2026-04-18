# Redesign chat/agent workflow to work for all endurance athletes

## Summary

The current chat onboarding and planning workflow is implicitly cycling-only and assumes metric literacy (FTP in watts, weight in kg) that most endurance athletes don't have. It also follows a rigid field-by-field questionnaire that doesn't adapt to what the athlete actually tells it. This needs a significant redesign before the product is usable by a broad audience of runners, triathletes, cyclists, swimmers, rowers, and other endurance athletes.

## What's wrong today

### The profile model is cycling-centric

`AthleteProfile` has exactly one sport-specific field: `cycling_ftp_watts`. It is the **second question asked** — immediately after goals. Athletes who don't cycle have no answer for it, and most recreational athletes who do cycle won't know their FTP either.

### The questionnaire is a rigid waterfall

`_profile_field_order` in `backend/services/chat.py` defines a fixed sequence:
```
goals → cycling_ftp_watts → weight_kg → age → constraints → injuries_rehab → notes
```
The agent blocks on each field in order. If an athlete says "I'm a runner" in their goals, the very next message is still "what's your current cycling FTP in watts?" — which is confusing and erodes trust immediately.

### Training load has a single proxy

The planner uses `cycling_ftp_watts` as the only baseline fitness signal. There's no way to express current training load for non-cyclists: weekly mileage, recent race times, pace zones, perceived effort, hours per week, VO2max estimate, run/swim/row pace, heart rate zones, etc.

### Units are metric-only without explanation

Weight is collected in kilograms. There's no conversion or prompt for athletes who think in pounds, stone, or who simply don't want to provide weight at all.

## What the redesign should achieve

### 1. Sport selection as the first branch point

After capturing goals, ask what sport(s) the athlete trains for. This gates all downstream questions:

| Sport | Relevant load proxy |
|---|---|
| Cycling | FTP (watts) — if they know it; otherwise hours/week + perceived difficulty |
| Running | Weekly mileage + recent race time or easy pace |
| Triathlon | All three sports, abbreviated |
| Swimming | Weekly yardage/meterage or pace per 100 |
| Rowing | 2k erg time or watts |
| General / multi-sport | Hours/week at easy, moderate, hard effort (RPE-based) |

### 2. Progressive approximation for training load

No athlete should be blocked by a metric they don't have. The agent should cascade:

1. Ask for the precise metric (FTP, pace, etc.)
2. If they don't know it, fall back to: "how many hours a week do you train, and how would you describe the intensity — mostly easy, mixed, or hard?"
3. Use that to derive a rough load estimate the planner can work with

### 3. Conversational profile filling, not a form

The agent should be able to extract profile fields from natural language rather than requiring structured answers in a fixed order. If an athlete says "I'm a 35-year-old runner training for my first marathon, running about 40 miles a week," the agent should be able to extract age, sport, goals, and current load in one turn rather than asking four sequential questions.

### 4. Optional fields stay optional

Weight, age, and precise power/pace metrics should be explicitly framed as optional. The planner should produce a reasonable plan without them.

### 5. Planner receives sport context

`AdaptedPlan` and `AthleteProfile` need to carry sport type so the generated plan uses the right terminology, units, and workout types (intervals vs. long run vs. brick session vs. easy spin).

## Scope of changes

**Backend:**
- `AthleteProfile` model: add `sport` (enum or free text), `weekly_hours`, `perceived_intensity`, `recent_race_or_benchmark` (free text), deprecate or make `cycling_ftp_watts` sport-gated
- `ChatService`: replace `_profile_field_order` waterfall with a more flexible extraction loop that branches on sport and cascades on load metrics
- `PlannerService`: use sport type and load proxy to generate sport-appropriate plans with correct terminology

**Frontend / UX:**
- The chat welcome message should set expectations: "I'll ask a few questions to understand your background — none of them are required, but the more context you give me, the better I can coach you."

## What to keep

- The conversational, single-thread-per-athlete model is correct — don't add forms
- Structured profile persistence (Supabase) is correct — the agent should still save extracted fields
- The check-in + plan generation flow is correct

## Related

- `backend/services/chat.py` — `_profile_field_order`, `_question_for_field`, `_field_label`, `_parse_profile_field`
- `backend/models/planning.py` — `AthleteProfile`, `AdaptedPlan`
- `backend/services/planner.py` — plan composition
