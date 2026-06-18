# OpenAI API Quality, Latency, Reliability, and Cost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the coaching agent's response quality, time to first token, failure handling, and OpenAI spend while preserving the current user-visible coaching and persistence behavior.

**Architecture:** Keep Supabase chat history as the durable source of truth and keep the Vercel AI SDK Responses API orchestration in TypeScript. Introduce explicit model policy, parallel and failure-tolerant specialist execution, reusable Tavily MCP lifecycle management, cached image extraction, and athlete home-timezone context. Continue sending the original image to the lead coach when extraction confidence is low.

**Tech Stack:** Next.js 15 App Router, TypeScript, Vercel AI SDK 6, `@ai-sdk/openai`, Zod, FastAPI, Pydantic, Supabase/Postgres, Vitest, pytest.

---

## Confirmed product decisions

- The lead coach uses `gpt-5.5` through the Responses API.
- Lead-coach reasoning effort is configurable only within `none`, `minimal`, `low`, and `medium`; it must never exceed `medium`. Production defaults to `medium`.
- Specialist models remain independently configurable so cost and latency can be tuned without changing the lead model.
- A specialist failure degrades gracefully. Successful reports continue to the lead coach; a failed specialist does not make the whole chat request return 503.
- Low-confidence screenshot extraction preserves current behavior by retaining the original image for the lead coach.
- Tavily MCP remains the web-search provider. Its client/tool discovery lifecycle should be reused within a warm server process through an encapsulated, dependency-injectable provider, not ad-hoc `globalThis` state.
- OpenAI-managed response retention is acceptable, but Supabase remains the durable conversation source of truth. `previous_response_id` adoption is deferred until rehydration and failover behavior are designed and tested.
- Athlete profiles gain a canonical IANA `home_timezone`. Browser timezone is turn context, not an automatic profile overwrite. The coach asks whether to update home timezone only when the difference matters, such as travel, relative-day planning, or a session crossing local midnight.
- Broad telemetry, dashboards, and SLO enforcement are out of scope. Minimal structured error logging needed to operate retries and fallbacks remains in scope.
- OpenAI hosted web search is not part of this implementation. A GitHub issue will track a Tavily-versus-hosted-search evaluation.

## Current flow and target boundaries

The browser sends AI SDK `UIMessage` objects to `app/api/chat/route.ts`. The route resolves the browser session, selects recent messages, analyzes images through the Python engine, persists the user turn, loads athlete context, discovers Tavily MCP tools, and calls `streamCoachTurn`. The orchestrator routes intent, runs zero or more specialists, streams the lead response, executes coaching tools, and persists the completed assistant `parts[]`.

The target architecture keeps those boundaries but makes five changes:

1. Model and request policy becomes explicit and testable.
2. Independent pre-stream work and specialist calls execute concurrently where safe.
3. Image extraction becomes strict, reusable, and avoids reanalysis on later turns.
4. Tavily tool discovery is lazy and reused per warm process through a focused provider abstraction.
5. Home and browser timezone context is available without silently changing profile data.

## Task 1: Centralize OpenAI model and request policy

**Files:**

- Create: `lib/agent/model-policy.ts`
- Modify: `lib/agent/orchestrator.ts`
- Modify: `lib/agent/specialists.ts`
- Modify: `backend/config.py`
- Modify: `backend/engine/screenshot_analyzer.py`
- Modify: `.env.example`
- Test: `tests/web/agent-model-policy.test.ts`
- Test: `tests/web/orchestrator.test.ts`
- Test: `tests/python/test_screenshot_analyzer.py`

- [ ] Add failing tests asserting that the lead model is `gpt-5.5`, production reasoning defaults to `medium`, and no configured value can exceed `medium`.
- [ ] Run `bun run test tests/web/agent-model-policy.test.ts tests/web/orchestrator.test.ts` and confirm the new assertions fail because no policy module exists.
- [ ] Implement a typed policy with this public shape:

```ts
export type BoundedReasoningEffort = "none" | "minimal" | "low" | "medium";

export type AgentModelPolicy = {
  leadModel: string;
  leadReasoningEffort: BoundedReasoningEffort;
  specialistModel: string;
  specialistReasoningEffort: BoundedReasoningEffort;
  textVerbosity: "low" | "medium";
};

export function loadAgentModelPolicy(
  env: Readonly<Record<string, string | undefined>> = process.env,
): AgentModelPolicy;
```

- [ ] Pass OpenAI provider options explicitly to `streamText` and `generateText`; set bounded retries, output-token limits, and total/step/chunk timeouts at each call site.
- [ ] Add Python settings for the vision model and timeout. Keep vision on a smaller model initially and make the value environment-configurable.
- [ ] Run the targeted TypeScript and Python tests and confirm they pass.
- [ ] Commit as `feat(agent): configure OpenAI model policy`.

## Task 2: Make specialist execution parallel and failure-tolerant

**Files:**

- Modify: `lib/agent/specialists.ts`
- Modify: `lib/agent/orchestrator.ts`
- Test: `tests/web/agent-specialists.test.ts`
- Test: `tests/web/orchestrator.test.ts`

- [ ] Add failing tests proving selected specialists start without waiting for earlier roles, successful reports retain `SPECIALIST_ORDER`, and one rejected generation does not reject the entire specialist run.
- [ ] Run the targeted tests and confirm failure under the current serial `for` loop.
- [ ] Replace serial execution with settled parallel execution. Parse each successful output, log only role and safe error class for failures, and return successful reports in deterministic order.
- [ ] Keep lead behavior unchanged when all specialists succeed. When all fail, supply an empty report list so the lead coach can still answer from athlete context and tools.
- [ ] Run specialist, orchestrator, prompt, and schema tests.
- [ ] Commit as `perf(agent): parallelize specialist reports`.

## Task 3: Reuse Tavily MCP tool discovery safely

**Files:**

- Create: `lib/agent/tavily-tools.ts`
- Modify: `app/api/chat/route.ts`
- Modify: `lib/site.ts`
- Test: `tests/web/tavily-tools.test.ts`
- Test: `tests/web/chat-route.test.ts`

- [ ] Add failing tests proving concurrent callers share one MCP initialization, successful tool discovery is reused, a rejected initialization is cleared for a later retry, and missing configuration returns an empty tool set.
- [ ] Implement `createTavilyToolProvider` as a dependency-injectable closure or class. It owns a lazy promise and optional `close()` method; it does not write to `globalThis` and does not expose the Tavily API key.
- [ ] Create one route-module provider instance so warm Next.js server instances reuse discovery while cold starts initialize normally.
- [ ] Preserve Tavily tool names and behavior supplied to `streamCoachTurn`.
- [ ] Run route and provider tests.
- [ ] Commit as `perf(chat): reuse Tavily MCP tools`.

## Task 4: Make screenshot extraction strict and reusable

**Files:**

- Create: `supabase/migrations/0005_chat_image_extractions.sql`
- Modify: `docs/supabase-migration-history.md`
- Modify: `backend/models/chat.py`
- Modify: `backend/repos/supabase_repo.py`
- Modify: `backend/engine/screenshot_analyzer.py`
- Modify: `api/index.py`
- Modify: `app/api/chat/route.ts`
- Modify: `lib/agent/message-context.ts`
- Test: `tests/python/test_screenshot_analyzer.py`
- Test: `tests/python/test_supabase_repo.py`
- Test: `tests/web/chat-route.test.ts`

- [ ] Add failing tests for strict classification/extraction schemas, reuse by stable image URL or object key, and low-confidence fallback retaining the original image part.
- [ ] Add a focused extraction cache keyed by user and stable object identity, with analyzer version and timestamps. Apply row-level ownership rules consistent with chat messages.
- [ ] Use Responses API Structured Outputs for both classification and extraction. Retain the two-step flow initially to preserve behavior; cache both results so each uploaded image is analyzed only once per analyzer version.
- [ ] Return a typed extraction result including confidence and whether the original image should remain available to the lead.
- [ ] Append cached extraction text to model context. Do not strip the original image when confidence is below the existing threshold.
- [ ] Update migration history in the same commit.
- [ ] Run targeted Python and web tests, then `bun run check` and the full Python suite.
- [ ] Commit as `perf(vision): cache structured screenshot extraction`.

## Task 5: Add athlete home timezone and browser-turn context

**Files:**

- Create: `supabase/migrations/0006_athlete_home_timezone.sql`
- Modify: `docs/supabase-migration-history.md`
- Modify: `backend/models/athlete.py`
- Modify: `backend/repos/supabase_repo.py`
- Modify: `backend/engine/context.py`
- Modify: `api/index.py`
- Modify: `lib/types.ts`
- Modify: `lib/schemas.ts`
- Modify: `lib/agent/types.ts`
- Modify: `lib/agent/system-prompt.ts`
- Modify: `lib/agent/tools.ts`
- Modify: `components/coach-chat.tsx`
- Modify: `app/api/chat/route.ts`
- Test: `tests/python/test_api.py`
- Test: `tests/python/test_supabase_repo.py`
- Test: `tests/web/chat-route.test.ts`
- Test: `tests/web/agent-system-prompt.test.ts`
- Test: `tests/web/dashboard.test.tsx`

- [ ] Add failing tests for IANA timezone validation, profile round-trip persistence, browser timezone in the chat request, and prompt rules that distinguish home from observed browser timezone.
- [ ] Add nullable `home_timezone` to athlete profiles and document migration ordering.
- [ ] Extend profile update schemas and tools so the coach can persist a confirmed home timezone.
- [ ] Send `Intl.DateTimeFormat().resolvedOptions().timeZone` in the chat request body.
- [ ] Add browser timezone to per-turn context without persisting it automatically.
- [ ] Replace the UTC-only prompt date with a date computed in home timezone when present, otherwise browser timezone, otherwise UTC.
- [ ] Instruct the coach not to mention a timezone difference unless the user mentions travel, relative-day scheduling, late-night timing, or the difference crosses a calendar date. Ask before changing `home_timezone`.
- [ ] Run targeted web and Python tests, followed by full checks.
- [ ] Commit as `feat(profile): add athlete home timezone`.

## Task 6: Parallelize independent route preparation and improve bounded errors

**Files:**

- Modify: `app/api/chat/route.ts`
- Modify: `lib/agent/orchestrator.ts`
- Modify: `lib/errors.ts`
- Modify: `components/coach-chat.tsx`
- Test: `tests/web/chat-route.test.ts`
- Test: `tests/web/orchestrator.test.ts`
- Test: `tests/web/dashboard.test.tsx`

- [ ] Add failing tests proving user persistence, athlete-context loading, image extraction, and Tavily discovery overlap after authentication where dependencies allow.
- [ ] Add tests for stable public error categories covering authentication, unreadable images, transient model failures, tool failures, and interrupted streams.
- [ ] Parallelize independent preparation without changing the rule that the latest user message is persisted best-effort and assistant messages persist only after completed streams.
- [ ] Preserve upstream details only in sanitized server logs. Return concise recovery guidance to the UI.
- [ ] Keep broad usage telemetry and dashboards out of this change.
- [ ] Run all targeted tests and full repository checks.
- [ ] Commit as `perf(chat): reduce pre-stream latency`.

## Task 7: Evaluate stateful Responses API adoption without changing the source of truth

**Files:**

- Create: `docs/architecture/openai-response-state.md`
- Test: add a lightweight validation script only if the document identifies a behavior-compatible rehydration design.

- [ ] Document how `previous_response_id`, OpenAI retention, Supabase history, retries, cross-device sessions, model changes, and deleted messages would interact.
- [ ] Keep this phase documentation-only unless replay and failover preserve the current database-backed behavior.
- [ ] Commit as `docs(agent): evaluate Responses conversation state`.

## Verification and rollout

- [ ] Run `bun run check`.
- [ ] Run `UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run ruff check .`.
- [ ] Run `UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run ruff format --check .`.
- [ ] Run `UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run ty check`.
- [ ] Run `UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run pytest`.
- [ ] Run copy-paste detection and Knip using the repository's pre-push configuration.
- [ ] Run Playwright because attachment, error, and timezone request behavior affects the UI.
- [ ] Test representative onboarding, workout, recovery, plan-change, Tavily, and screenshot turns in preview.
- [ ] Confirm the lead request uses `gpt-5.5` and reasoning effort never exceeds `medium`.
- [ ] Push each verified commit to `codex/openai-api-quality-latency`.

## Expected benefits and tradeoffs

| Change                              | Expected benefit                                                       | Tradeoff                                                           |
| ----------------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------ |
| GPT-5.5 lead with bounded reasoning | Better tool use and coaching synthesis with a hard reasoning ceiling   | More expensive than smaller models; requires concise output limits |
| Parallel, settled specialists       | Lower time to first token and graceful degradation                     | Higher burst concurrency and possible rate-limit pressure          |
| Reusable Tavily provider            | Avoid repeated MCP discovery during warm invocations                   | Lifecycle is process-local and resets on serverless cold starts    |
| Cached structured image extraction  | Fewer vision calls, lower cost, predictable parsing                    | Adds storage, versioning, and invalidation logic                   |
| Home plus browser timezone          | Correct relative dates without silently changing athlete identity data | Adds schema and conversational edge cases                          |
| Bounded retries and errors          | Better transient-failure recovery and clearer user actions             | Retries can increase tail latency if limits are too generous       |
| Deferred `previous_response_id`     | Preserves current recovery and cross-device semantics                  | Leaves some potential context-cost savings unrealized              |

## Follow-up issue

Create a GitHub issue to compare Tavily MCP with OpenAI hosted web search. The issue must cover response quality, citations, supported domain controls, latency, per-query cost, retention/privacy, AI SDK integration, tool-result persistence, and a representative coaching-query evaluation before any provider change.
