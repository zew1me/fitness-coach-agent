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
  `prepareHistoryItemForModelInput()`. The latter restores raw compacted
  `function_call_output` items to SDK `function_call_result` items, including
  their call IDs and structured text/image/file output, so retained tool-call
  pairs remain replayable. This is why the file isn't named compaction-specific.
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
- `estimatedTokens >= autoCompactTokens` (default 60 000)
- `nonUserItemCount >= autoCompactNonUserItems` (default 40)

The token threshold is intentionally well below the model context window. A
single coach turn can replay durable history through the delegation planner,
the lead coach, and a post-tool follow-up in the same one-minute quota window.
Compacting around 60 000 estimated history tokens leaves headroom for those
additional requests under the current 200 000 TPM model quota; waiting until a
context-window threshold would allow one turn to consume the quota several
times over.

`estimateStoredContext` currently estimates from serialized UTF-8 bytes. Every
completed compaction compares the API's authoritative `usage.input_tokens` with
both the raw stored-item estimate (`stored_*`) used by the trigger and the
sanitized Responses request estimate (`prepared_*`). Each series logs
`*_estimate_minus_actual_tokens`, `*_estimate_error_percent`, and
`*_estimated_to_actual_ratio`, so request-shape sanitization can be separated
from token-estimation error. Positive signed error means the estimate was
conservative; negative means it under-counted. Collect at least seven days of
production samples before applying a correction factor or replacing the
estimator, then re-derive both the compaction and cold-seed budgets from the
calibrated token count rather than guessing from JSON shape.

`responses.compact` output is the **canonical replacement window**. OpenAI may
retain prior messages or tool-call pairs alongside its opaque compaction item,
but does not guarantee that any particular input item survives. The app stores
the returned output wholesale with `replaceAll`; it never appends dropped input
items or reconstructs the pre-compaction transcript. Splicing old items back in
would undo compaction and could produce duplicate or otherwise invalid history.
New turn items are appended normally only after this replacement is complete.

Safety guard: if `responses.compact` returns an empty array the method **throws**
rather than replacing durable context with nothing. This prevents a model error
or API glitch from silently erasing the conversation.

Compaction defaults to `gpt-5.6-luna`, the cost-sensitive GPT-5.6 tier, unless
the session supplies an explicit model override. Its raw OpenAI client allows four
retries (and honors provider `Retry-After` guidance) so transient rate limits do not
unnecessarily abort the pre-turn compaction step. Compaction does not use the
user-facing coach model fallback ladder.

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
3. On a cold session, seed only the newest transcript suffix that fits the
   60 000-token compaction threshold, then project stored items + incoming messages
4. Run the pre-turn compaction check on every turn
      ├─ force compaction if the projection is ≥ 60 000 estimated tokens
      ├─ otherwise compact when stored history has ≥ 40 non-user items
      └─ if required compaction fails, degrade to stateless (turn still answers),
         except a 409 conflict or aborted signal still ends the turn
5. Agent runs     →  SDK appends items via addItems() during the turn
      └─ lead model honors provider retry delays up to 8 seconds, then falls back
         only if no stream event, response text, or tool call has started
      └─ longer provider delays skip the in-request retry; if every tier is exhausted,
         the athlete-visible error reports the longest provider wait seen across the ladder
6. After a successful turn, auto-compaction runs if the same thresholds are hit
7. Release lease  →  DELETE /api/chat/model-state/lease  (always in finally)

The pre-turn check is required even though the SDK also compacts after a
successful turn. A turn that exceeds TPM can fail after appending tool-call
items but before the SDK reaches post-turn compaction. If the next request only
checks a high context-window threshold, the durable state becomes trapped in a
fail/retry loop. Pre-turn compaction lets that state recover; if the compaction
request itself is rate-limited, stateless degradation prevents a user-visible
failure and leaves the stored state intact for a later retry. As with every
durable setup step, a 409 conflict or aborted signal still ends the turn because
continuing without ownership would be unsafe.
```

Thresholds are declared by `DurableCompactionSession` and reused by the
orchestrator for its projected pre-turn check. Compaction runs at the token
threshold or the non-user-item threshold; the orchestrator forces it when the
projection (stored history plus the incoming turn) reaches the token threshold.

### Setup is best-effort — only a 409 or an abort ends the turn

A durable session is an optimisation, not a prerequisite for answering the
athlete, so **every** step of steps 1–4 above shares one failure policy: log,
tag the Sentry event `degrading: "true"` with the failing `step`
(`seed` / `project` / `compact`), and run the turn statelessly
(`prepareDurableSession` in `lib/agent/orchestrator.ts`). Exactly two failures
still end the turn, because in both cases continuing is unsafe rather than
merely degraded:

- **Any 409** — another turn owns this chat's session, and two turns writing the
  same durable state concurrently would corrupt it. This covers both lease
  acquisition and a `ModelStateError` with `status === 409` escaping a setup
  write (`addItems` while seeding, `replaceAll` during forced compaction), which
  means the CAS/lease check rejected the write after exhausting its retries.
- **An aborted signal** — the caller cancelled, or renewal lost the lease
  mid-turn (`onLeaseLost`), so this turn no longer owns what it is writing.

Degrading costs this turn's items: they never reach `chat_model_states`, and in
the `session` context strategy the model sees only the latest user turn plus
athlete context. That is deliberate and **recoverable** — stored state is left
untouched, so the next turn seeds from the full transcript (a cold seed only
happens when stored items are empty) or retries compaction normally. Partial
seeding was considered and rejected: it would write a truncated history that is
never re-seeded, trading a one-turn degradation for permanent context loss.

For the same reason `DurableCompactionSession` builds its `OpenAI` client
lazily, on first compaction rather than in the constructor — `new OpenAI()`
throws without credentials, and most turns never reach a compaction threshold at
all. See issue #408.

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

| File                                           | What it covers                                                                                                                                                                                                                        |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/web/supabase-agent-session.test.ts`     | Unit: CAS retries, exact `replaceAll`, coaching-memory isolation, and deterministic replay conversion for optionally retained raw tool pairs                                                                                          |
| `tests/web/durable-compaction-session.test.ts` | Unit: canonical output replacement, trigger thresholds, provider-metadata stripping, compact API shape conversion, and `estimateStoredContext`                                                                                        |
| `tests/web/orchestrator.test.ts`               | Turn-level: durable setup degrades to stateless on unreadable model state, unfetchable transcript, and failed required pre-turn compaction; wrapped post-tool 429s produce a deterministic acknowledgement; still aborts on 409/abort |
| `tests/integration/oai-agents.test.ts`         | Live OpenAI: compaction API compatibility and continuation from the canonical compacted window; never assumes retention of a particular input item                                                                                    |
| `tests/web/real-durable-session.test.ts`       | Optional live continuity probe with delegation and prior-detail recall                                                                                                                                                                |
| `tests/web/coaching-memory.test.ts`            | Memory operation types and merge logic                                                                                                                                                                                                |
| `tests/python/test_supabase_repo.py`           | Repo CAS, stale-version rejection, lease acquisition/release, transcript isolation                                                                                                                                                    |
| `tests/python/test_chat_service.py`            | Service layer: model state CRUD, lease service methods                                                                                                                                                                                |

The `@pytest.mark.db` tests (live DB) are excluded from the default `pytest`
run (`addopts = "-m 'not db'"` in `pyproject.toml`). Run them explicitly with
`bun run test:db` against a local or preview Supabase project.
