# Durable Conversation Compaction Design

This document explains how the fitness-coach agent persists, replays, and
compacts conversation context across HTTP requests without rewriting the
athlete-visible chat transcript.

## Motivation

Previous to this, we would send the OpenAI Agents SDK the full item history on every turn.
Without compaction, history grows without bound; once the context window of the underlying
model is approached the request will either fail or degrade. We also see latency increase,
as well as token usage as the conversation history goes. Given the way this app is modeled
as a single chat thread (maybe in the future this changes, but there will still be a single
primary chat thread in most incantations I can think of), user experience decreases due to
latency the longer they use the app. Compaction shrinks model history automatically while
the user's visible message thread (`chat_messages`) remains intact.

---

## Two separate stores

| Store                      | Table               | Written by         | Compactable                   |
| -------------------------- | ------------------- | ------------------ | ----------------------------- |
| Athlete-visible transcript | `chat_messages`     | Repo on every turn | **No** — append-only          |
| Private agent replay state | `chat_model_states` | Repo on every turn | **Yes** — replaced atomically |

Compaction only ever rewrites `chat_model_states.items`. The `chat_messages`
table is never touched by a compaction operation.

---

## Database schema — `chat_model_states`

Key columns (see `supabase/migrations/20260625172251_chat_model_state.sql`):

| Column                | Purpose                                                            |
| --------------------- | ------------------------------------------------------------------ |
| `thread_id`           | FK → `chat_threads.id`; also used as the Agents SDK session ID     |
| `user_id`             | Unique index; the primary lookup key for in-progress requests      |
| `items`               | `jsonb` — the Agents SDK `AgentInputItem[]` replay log             |
| `coaching_memory`     | `jsonb` — structured long-term memory; never touched by compaction |
| `compaction_metadata` | `jsonb` — audit trail written on each compaction                   |
| `version`             | `bigint` — monotonically-increasing CAS counter                    |
| `lease_id`            | Identifies the active turn; `null` when idle                       |
| `lease_expires_at`    | Hard expiry on the lease; allows recovery from crashed workers     |

---

## Optimistic concurrency — CAS + lease

Every write to `chat_model_states` must match the current `version` **and** the
active `lease_id`. The repo implements this as a conditional `UPDATE`:

```python
# backend/repos/supabase_repo.py ~L585-600
.eq("version", expected_version)
.eq("lease_id", lease_id)
.gt("lease_expires_at", datetime.now(UTC).isoformat())
```

If the row was updated by another request between load and write the condition
fails (zero rows updated) and the repo raises `ValueError`. The FastAPI handler
(`api/index.py`) maps that `ValueError` to **HTTP 409 Conflict**.

The TypeScript `SupabaseAgentSession.mutate()` retries on 409 up to
`maxCasRetries` times (default 3), force-reloading state before each attempt.
This makes writes idempotent under concurrent racing turns.

### Turn lease lifecycle

```text
POST /api/chat/model-state/lease   →  acquire lease (TTL 300 s)
        ↓ turn runs
PUT /api/chat/model-state          →  write items/memory (lease checked atomically)
        ↓
DELETE /api/chat/model-state/lease →  release lease (always in finally block)
```

Leases expire automatically; a crashed worker holding a stale lease will be
preempted once `lease_expires_at` passes and another turn acquires a new lease.

---

## TypeScript layer

This subsystem is split across three files with a one-directional dependency
graph: `responses-item-shapes.ts` has no dependents within this trio and is
imported by both of the other two — there is no reverse edge, so there's no
circular-import risk between the CRUD session and its compaction wrapper.

### `responses-item-shapes.ts` (`lib/agent/responses-item-shapes.ts`)

Pure leaf module — only depends on `@openai/agents` types. Converts between
Agents SDK item shapes and raw OpenAI Responses API shapes:

- `unsupportedFileContentToText`, `prepareFunctionItemForModelInput` — run on
  **every model turn** (not just compaction) via
  `prepareHistoryItemForModelInput()`, which is why this file isn't named
  compaction-specific.
- `toResponsesCompactInputItem`, `sanitizeResponsesCompactInputItem` — used
  only by `DurableCompactionSession` to build the `responses.compact` request.

### `SupabaseAgentSession` (`lib/agent/supabase-agent-session.ts`)

Implements `SessionHistoryRewriteAwareSession` (the Agents SDK interface):

- `getItems()` — returns the stored `items` array (optionally tail-sliced).
- `addItems()` / `popItem()` — mutate items via CAS.
- `replaceAll(items, metadata)` — atomically replace the full items array and
  merge metadata into `compaction_metadata`. Preserves `coaching_memory`.
- `applyHistoryMutations()` — rewrites specific `function_call` items in-place
  (used by the SDK for tool-result redaction).
- `prepareHistoryItemForModelInput()` — strips `input_image` parts and
  delegates unsupported-file/function-item shape fixing to
  `responses-item-shapes.ts` before passing history to the model (images are
  stored in R2, not replayed).

This file is CRUD-only; it has no knowledge of `OpenAI.responses.compact`.

### `DurableCompactionSession` (`lib/agent/durable-compaction-session.ts`)

Wraps a `SupabaseAgentSession`-shaped session (typed structurally, not as the
concrete class, precisely to keep this a one-directional dependency) and
implements `OpenAIResponsesCompactionAwareSession`. Also home to
`estimateStoredContext`/`StoredContextEstimate`, since token/byte estimation
only matters for compaction's trigger conditions. The key method:

```text
runCompaction(args?) → OpenAIResponsesCompactionResult | null
```

Trigger conditions (any one is sufficient):

- `args.force === true` (explicit forced compaction)
- `estimatedTokens >= autoCompactTokens` (default 120 000)
- `nonUserItemCount >= autoCompactNonUserItems` (default 40)

Safety guard: if `responses.compact` returns an empty array the method **throws**
rather than replacing durable context with nothing. This prevents a model error
or API glitch from silently erasing the conversation.

**`previous_response_id` is never sent to `responses.compact`.** This session's
`input` (built by `buildCompactionInput` from the Supabase-stored `items`) is
always the complete, authoritative history — this app doesn't rely on
OpenAI's server-side conversation retention. `responses.compact` treats
`previous_response_id` as "layer `input` on top of the server-remembered
conversation for that response id," so passing both sends the same
conversation twice; the server can then hand back a compacted history with
the _same_ provider-assigned `id` on items from each copy, which the Responses
API rejects on the next replay with `400 Duplicate item found with id ...`.
The SDK's post-turn auto-compaction (`runCompactionOnSession` in
`@openai/agents-core`) always supplies a `responseId`, so `toOpenAICompactOptions`
deliberately drops it (and the `previous_response_id`/`store` fields) rather
than forwarding them.

**Self-heal for already-poisoned rows.** `SupabaseAgentSession.getItems()`
runs `dedupeItemsById` (`responses-item-shapes.ts`) on every read, keeping the
first occurrence of any item `id` and dropping later duplicates. This mirrors
the existing `input_file`-to-text self-heal: rows corrupted by the bug above
before this fix landed recover automatically on next read rather than needing
a per-environment data migration.

---

## Compaction flow (one turn)

```text
1. Acquire lease  →  POST /api/chat/model-state/lease
2. Load state     →  GET  /api/chat/model-state
3. Project token estimate for stored items + incoming messages
4. If estimate ≥ 220 000 tokens  →  force runCompaction() before agent run
      └─ hard limit 260 000: if compaction fails, throw (turn aborted)
      └─ soft limit 220 000: log Sentry warning, continue with uncompacted context
5. Agent runs     →  SDK appends items via addItems() during the turn
6. After the turn, auto-compaction runs if thresholds are hit
7. Release lease  →  DELETE /api/chat/model-state/lease  (always in finally)
```

Thresholds are declared in `lib/agent/orchestrator.ts` (forced pre-turn
compaction at soft and hard limit of tokens) and in the `DurableCompactionSession`
constructor defaults (auto-compaction at N tokens or M non-user items).

---

## Coaching memory

`coaching_memory` lives in `chat_model_states` but is intentionally **separate**
from `items`. It is never passed to `responses.compact`, so compaction cannot
summarize or lose structured athlete facts (goals, thresholds, injury history).

Operations on `coaching_memory` go through
`SupabaseAgentSession.updateCoachingMemory()` which merges updates via
`applyMemoryOperation()` in the same CAS write.

---

## Tests

| File                                           | What it covers                                                                                                                      |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `tests/web/supabase-agent-session.test.ts`     | Unit: CAS retries, replaceAll, coaching memory isolation                                                                            |
| `tests/web/durable-compaction-session.test.ts` | Unit: DurableCompactionSession trigger thresholds, provider-metadata stripping, compact API shape conversion, estimateStoredContext |
| `tests/web/real-durable-session.test.ts`       | Integration: full turn round-trip with fake repo                                                                                    |
| `tests/web/coaching-memory.test.ts`            | Memory operation types and merge logic                                                                                              |
| `tests/python/test_supabase_repo.py`           | Repo CAS, stale-version rejection, lease acquisition/release, transcript isolation                                                  |
| `tests/python/test_chat_service.py`            | Service layer: model state CRUD, lease service methods                                                                              |

The `@pytest.mark.db` tests (live DB) are excluded from the default `pytest`
run (`addopts = "-m 'not db'"` in `pyproject.toml`). Run them explicitly with
`bun run test:db` against a local or preview Supabase project.
