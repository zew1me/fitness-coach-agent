-- Finish the chat persistence cutover started by 0004_chat_messages_parts.sql (#303).
--
-- 0004 moved every message onto `chat_messages.parts` / `chat_messages.attachments`
-- (the AI SDK UIMessage shape) and kept two legacy structures "for one release
-- window": the denormalized `chat_messages.content` text mirror and the
-- `chat_attachments` table. Nothing reads either one any more, and nothing writes
-- `chat_attachments` at all, so drop both.
--
-- Deploy order: ship the application change that stops writing `content` BEFORE
-- applying this migration. Older application code still sends `content` on insert,
-- which PostgREST rejects once the column is gone.
--
-- Both drops are irreversible, so refuse to run while either structure still holds
-- data that the replacement columns do not carry. Each guard aborts the whole
-- migration (it runs in one transaction) rather than losing a row.

do $$
declare
  unmigrated_text bigint;
  unmigrated_attachments bigint;
begin
  -- Dynamic SQL so the guards compile even if a structure is already gone,
  -- keeping the migration safe to re-run (matching the `if exists` drops below).
  if exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'chat_messages'
      and column_name = 'content'
  ) then
    -- `content` was always written as the in-order concatenation of the text
    -- parts (0004 backfilled one text part from it; the backend derived it from
    -- `parts` since), so any non-empty value that differs from that
    -- concatenation is text `parts` does not carry.
    execute $q$
      select count(*)
      from public.chat_messages m
      where coalesce(m.content, '') <> ''
        and m.content is distinct from (
          select coalesce(string_agg(coalesce(p.part ->> 'text', ''), '' order by p.ord), '')
          from jsonb_array_elements(m.parts) with ordinality as p(part, ord)
          where p.part ->> 'type' = 'text'
        )
    $q$ into unmigrated_text;

    if unmigrated_text > 0 then
      raise exception
        'refusing to drop chat_messages.content: % row(s) have content that their text parts do not reproduce',
        unmigrated_text;
    end if;
  end if;

  if to_regclass('public.chat_attachments') is not null then
    -- Every legacy row must appear, by object key, in its message's
    -- `attachments` JSON (0004 backfilled it as `objectKey`).
    execute $q$
      select count(*)
      from public.chat_attachments a
      join public.chat_messages m on m.id = a.message_id
      where not exists (
        select 1
        from jsonb_array_elements(m.attachments) as e(entry)
        where coalesce(e.entry ->> 'objectKey', e.entry ->> 'object_key') = a.object_key
      )
    $q$ into unmigrated_attachments;

    if unmigrated_attachments > 0 then
      raise exception
        'refusing to drop chat_attachments: % row(s) are missing from their message''s attachments JSON',
        unmigrated_attachments;
    end if;
  end if;
end;
$$;

-- `cascade` also removes the table's RLS policy (20260816191358_rls_and_security.sql)
-- and its index; no view, function, or foreign key references it.
drop table if exists public.chat_attachments cascade;

alter table public.chat_messages
  drop column if exists content;
