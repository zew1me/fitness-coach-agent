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
    execute $q$
      select count(*)
      from public.chat_messages
      where coalesce(content, '') <> ''
        and parts = '[]'::jsonb
    $q$ into unmigrated_text;

    if unmigrated_text > 0 then
      raise exception
        'refusing to drop chat_messages.content: % row(s) have text in content but no parts',
        unmigrated_text;
    end if;
  end if;

  if to_regclass('public.chat_attachments') is not null then
    execute $q$
      select count(*)
      from public.chat_attachments a
      join public.chat_messages m on m.id = a.message_id
      where m.attachments = '[]'::jsonb
    $q$ into unmigrated_attachments;

    if unmigrated_attachments > 0 then
      raise exception
        'refusing to drop chat_attachments: % row(s) belong to messages with no attachments JSON',
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
