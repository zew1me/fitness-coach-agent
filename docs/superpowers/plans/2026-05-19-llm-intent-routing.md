# LLM Intent Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace deterministic keyword-based chat intent routing with a structured, model-backed classifier that chooses internal specialists reliably and falls back safely.

**Architecture:** Add a non-streaming AI SDK structured-output classifier before specialist execution. Keep routing mechanics deterministic after classification: validate with Zod, normalize specialist ordering, enforce recovery-before-workout, and fall back to lead-only routing when classification fails. Preserve the existing orchestrator, specialist report, context slice, Tavily, preview-bypass, and image-extraction flow.

**Tech Stack:** Next.js route handlers, TypeScript, Vercel AI SDK `generateText` with `Output.object`, Zod, Vitest.

---

## File Structure

- Modify `lib/agent/orchestration-types.ts`
  - Add schema and type for intent classifier output.
  - Include `confidence` and `rationale` for observability and tests.
- Replace `lib/agent/intent-router.ts`
  - Remove regex keyword matching.
  - Add async `routeTurnIntent(...)` that calls the classifier model.
  - Add pure normalization helpers for specialist ordering and fallback.
- Modify `lib/agent/orchestrator.ts`
  - Await `routeTurnIntent(...)`.
  - Pass the same model used by the turn for v1.
- Modify `lib/agent/system-prompt.ts`
  - Add `buildIntentClassifierPrompt(...)` or a dedicated router prompt builder.
- Modify `tests/web/agent-intent-router.test.ts`
  - Mock AI SDK output and test schema-driven routing, ordering, and fallback.
- Modify `tests/web/chat-route.test.ts` or add orchestrator-focused tests if needed
  - Verify orchestration still delegates selected model-window messages correctly.

---

### Task 1: Add Intent Classification Schema

**Files:**
- Modify: `lib/agent/orchestration-types.ts`
- Test: `tests/web/agent-intent-router.test.ts`

- [ ] **Step 1: Write failing schema tests**

Add tests that parse:
- valid classifier output with `kind`, `specialists`, `confidence`, and `rationale`
- invalid specialist role
- invalid `kind`
- duplicate specialists that will later be normalized by code, not schema

Expected initial result: tests fail because the schema does not exist.

- [ ] **Step 2: Implement schema**

Add:

```ts
export const turnIntentClassificationSchema = z
  .object({
    confidence: z.enum(["low", "medium", "high"]),
    kind: z.enum(["general", "intake", "mixed", "nutrition", "plan_change", "recovery", "workout"]),
    rationale: z.string().min(1),
    specialists: z.array(internalSpecialistRoleSchema).default([]),
  })
  .strict();

export type TurnIntentClassification = z.infer<typeof turnIntentClassificationSchema>;
```

- [ ] **Step 3: Run focused tests**

Run:

```bash
bun run test tests/web/agent-intent-router.test.ts
```

Expected: new schema tests pass; existing router tests may still fail until later tasks.

---

### Task 2: Add Intent Classifier Prompt Builder

**Files:**
- Modify: `lib/agent/system-prompt.ts`
- Test: `tests/web/agent-system-prompt.test.ts`

- [ ] **Step 1: Write failing prompt tests**

Add tests that assert the classifier prompt:
- defines intake, nutrition, recovery, and workout roles
- allows no specialists for general turns
- states recovery must precede workout for plan/workout changes
- tells the model not to persist data or write user-facing prose

- [ ] **Step 2: Implement prompt builder**

Add an exported function:

```ts
export function buildIntentClassifierPrompt(context: AthleteContextBundle): string {
  return [
    "Classify the latest athlete chat turn for internal routing.",
    "Return structured output only. Do not write user-facing prose. Do not call tools. Do not persist data.",
    "Specialists:",
    "- intake: onboarding, profile, goals, schedule, availability, athlete background.",
    "- nutrition: fueling, hydration, sodium, diet, restrictions, energy availability, protein.",
    "- recovery: sleep, HRV, soreness, fatigue, illness, stress, readiness, recovery constraints.",
    "- workout: completed activities, workout details, training plan creation, plan adjustment, thresholds, zones.",
    "Use no specialists for general acknowledgement, motivation, or simple questions the lead coach can answer directly.",
    "For plan creation, plan adjustment, or workout decisions affected by readiness, include recovery before workout.",
    `Athlete routing context: ${JSON.stringify({
      active_plan: context.active_plan,
      coaching_state: context.profile.coaching_state,
      goals: context.goals,
      primary_sports: context.profile.primary_sports,
      recent_recovery_count: context.recent_recovery.length,
    })}`,
  ].join("\\n\\n");
}
```

- [ ] **Step 3: Run prompt tests**

Run:

```bash
bun run test tests/web/agent-system-prompt.test.ts
```

Expected: prompt tests pass.

---

### Task 3: Replace Keyword Router With Structured Classifier

**Files:**
- Modify: `lib/agent/intent-router.ts`
- Test: `tests/web/agent-intent-router.test.ts`

- [ ] **Step 1: Write failing classifier tests**

Mock `generateText` so router tests cover:
- general output returns `specialists: []`
- nutrition output routes only nutrition
- plan-change output with `["workout", "recovery"]` normalizes to `["recovery", "workout"]`
- model error returns `{ kind: "general", specialists: [] }`
- malformed output returns `{ kind: "general", specialists: [] }`

- [ ] **Step 2: Implement async router**

Replace keyword logic with:

```ts
export async function routeTurnIntent({
  context,
  latestUserText,
  model,
}: RouteTurnIntentOptions): Promise<TurnIntent> {
  try {
    const { output } = await generateText({
      model,
      output: Output.object({ schema: turnIntentClassificationSchema }),
      prompt: latestUserText,
      system: buildIntentClassifierPrompt(context),
    });

    return normalizeTurnIntent(turnIntentClassificationSchema.parse(output));
  } catch {
    return { kind: "general", specialists: [] };
  }
}
```

Define `RouteTurnIntentOptions` with `context: AthleteContextBundle`, `latestUserText: string`, and `model: LanguageModel`.

- [ ] **Step 3: Implement normalization helpers**

Normalization rules:
- Deduplicate specialists.
- Use canonical order: intake, nutrition, recovery, workout.
- If both recovery and workout exist, recovery must come first.
- If `kind === "plan_change"`, include both recovery and workout.
- If no specialists remain, return `kind: "general"`.

- [ ] **Step 4: Run router tests**

Run:

```bash
bun run test tests/web/agent-intent-router.test.ts
```

Expected: all router tests pass without regex keyword matching.

---

### Task 4: Wire Async Router Into Orchestrator

**Files:**
- Modify: `lib/agent/orchestrator.ts`
- Test: existing `tests/web/chat-route.test.ts`; add `tests/web/agent-orchestrator.test.ts` only if route tests do not cover the changed contract.

- [ ] **Step 1: Update orchestrator call site**

Change:

```ts
const intent = routeTurnIntent(latestUserText(selectedMessages), context);
```

to:

```ts
const intent = await routeTurnIntent({
  context,
  latestUserText: latestUserText(selectedMessages),
  model,
});
```

- [ ] **Step 2: Verify specialist execution still receives normalized roles**

Add or update a test asserting `runSpecialists` receives `["recovery", "workout"]` for plan-change routing.

- [ ] **Step 3: Run focused chat/orchestrator tests**

Run:

```bash
bun run test tests/web/chat-route.test.ts tests/web/agent-intent-router.test.ts
```

Expected: tests pass.

---

### Task 5: Remove Keyword Routing Coverage and Add Failure Coverage

**Files:**
- Modify: `tests/web/agent-intent-router.test.ts`

- [ ] **Step 1: Delete tests that only prove keyword matching**

Remove tests whose only purpose is proving words like `vegetarian`, `HRV`, or `intervals` trigger a regex.

- [ ] **Step 2: Add classifier behavior tests**

Keep the same domain examples, but assert behavior by mocked structured output:
- classifier says nutrition -> nutrition
- classifier says recovery -> recovery
- classifier says workout -> workout
- classifier says mixed -> canonical order
- classifier throws -> general

- [ ] **Step 3: Run focused tests**

Run:

```bash
bun run test tests/web/agent-intent-router.test.ts
```

Expected: router tests pass and no longer depend on keywords.

---

### Task 6: Full Verification and PR Update

**Files:**
- No new files expected.

- [ ] **Step 1: Run full check**

Run:

```bash
bun run check
```

Expected: lint, typecheck, and Vitest all pass.

- [ ] **Step 2: Confirm no keyword router remains**

Run:

```bash
rg -n "includesAny|RegExp|vegetarian|interval|hrv|keyword|regex" lib/agent/intent-router.ts tests/web/agent-intent-router.test.ts
```

Expected: no keyword-routing implementation remains. Domain words may appear only in test prompt text, not as routing logic.

- [ ] **Step 3: Commit**

Run:

```bash
git add lib/agent/orchestration-types.ts lib/agent/intent-router.ts lib/agent/orchestrator.ts lib/agent/system-prompt.ts tests/web/agent-intent-router.test.ts tests/web/agent-system-prompt.test.ts tests/web/chat-route.test.ts
git commit -m "refactor chat intent routing through classifier"
```

- [ ] **Step 4: Push stacked branch**

Push on top of `codex/agent-orchestration-refactor` and open the implementation PR with base `codex/agent-orchestration-refactor`.

---

## Review Notes

- This plan intentionally does not add embeddings, training data, or durable classifier telemetry. Those are premature until real routing failures are observed.
- The classifier should use the same `gpt-5-mini` model as the turn for v1 to avoid configuration sprawl.
- The safety boundary remains specialist reports only: no specialist tools, no specialist persistence, and no client-supplied `user_id`.
