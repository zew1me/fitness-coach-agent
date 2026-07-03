-- Remove historical function tool-call items from durable model replay state.
--
-- `chat_model_states.items` is private model context. The athlete-visible
-- transcript remains in `chat_messages`, so dropping these replay-only tool
-- pairs is acceptable during alpha and prevents stale tool outputs from
-- poisoning future turns.
--
-- Preview affected rows before applying:
-- select thread_id, user_id
-- from public.chat_model_states
-- where items::text like '%"function_call%';
--
-- Rollout note: this migration increments chat_model_states.version for rows it
-- rewrites. Apply during a maintenance or low-traffic window, or briefly pause
-- durable chat writes, so in-flight optimistic-concurrency writes do not retry
-- against the rewrite.
with rewritten as (
  select
    state.thread_id,
    coalesce(
      jsonb_agg(item.value order by item.ordinality) filter (
        where item.value ->> 'type' not in (
          'function_call',
          'function_call_result',
          'function_call_output'
        )
      ),
      '[]'::jsonb
    ) as items
  from public.chat_model_states as state
  cross join lateral jsonb_array_elements(state.items) with ordinality as item(value, ordinality)
  where exists (
    select 1
    from jsonb_array_elements(state.items) as existing(value)
    where existing.value ->> 'type' in (
      'function_call',
      'function_call_result',
      'function_call_output'
    )
  )
  group by state.thread_id
)
update public.chat_model_states as state
set
  items = rewritten.items,
  version = state.version + 1,
  compaction_metadata = state.compaction_metadata || jsonb_build_object(
    'removed_tool_calls_from_model_state_at',
    timezone('utc', now())
  )
from rewritten
where state.thread_id = rewritten.thread_id;
