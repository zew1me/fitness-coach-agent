-- Remove stale alpha-era placeholder tool calls from durable model replay state.
--
-- Preview affected rows before applying:
-- select thread_id, user_id
-- from public.chat_model_states
-- where items::text like '%pending_implementation%';
--
-- These entries were created when unimplemented tools were advertised to the
-- coach and persisted as completed function-call outputs. They are not
-- user-visible transcript data; chat_messages remains untouched.
with stale_calls as (
  select
    state.thread_id,
    coalesce(item.value ->> 'call_id', item.value ->> 'callId') as call_id
  from public.chat_model_states as state
  cross join lateral jsonb_array_elements(state.items) as item(value)
  where item.value ->> 'type' in ('function_call_output', 'function_call_result')
    and item.value ->> 'output' like '%pending_implementation%'
    and coalesce(item.value ->> 'call_id', item.value ->> 'callId') is not null
),
rewritten as (
  select
    state.thread_id,
    coalesce(
      jsonb_agg(item.value order by item.ordinality) filter (
        where not (
          (
            item.value ->> 'type' in ('function_call_output', 'function_call_result')
            and item.value ->> 'output' like '%pending_implementation%'
          )
          or (
            item.value ->> 'type' = 'function_call'
            and coalesce(item.value ->> 'call_id', item.value ->> 'callId') in (
              select stale.call_id
              from stale_calls as stale
              where stale.thread_id = state.thread_id
            )
          )
        )
      ),
      '[]'::jsonb
    ) as items
  from public.chat_model_states as state
  cross join lateral jsonb_array_elements(state.items) with ordinality as item(value, ordinality)
  where exists (
    select 1
    from stale_calls as stale
    where stale.thread_id = state.thread_id
  )
  group by state.thread_id
)
update public.chat_model_states as state
set
  items = rewritten.items,
  version = state.version + 1,
  compaction_metadata = state.compaction_metadata || jsonb_build_object(
    'removed_pending_tool_outputs_at',
    timezone('utc', now())
  )
from rewritten
where state.thread_id = rewritten.thread_id;
