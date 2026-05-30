-- Adopt AI SDK UIMessage parts schema on chat_messages, mirroring vercel/chatbot's
-- Message_v2 table. Persisting `parts` and `attachments` as JSONB eliminates the
-- lossy translation between `UIMessage.parts[]` and (content + chat_attachments),
-- which currently drops inline images, tool-call pills, and reasoning blocks on
-- reload.

alter table public.chat_messages
  add column if not exists parts jsonb not null default '[]'::jsonb,
  add column if not exists attachments jsonb not null default '[]'::jsonb;

-- Backfill every existing row: collapse the prior `content` into a single text
-- part, and fold any joined chat_attachments rows into the new attachments JSON.
update public.chat_messages m
set parts = case
      when coalesce(m.content, '') = '' then '[]'::jsonb
      else jsonb_build_array(jsonb_build_object('type', 'text', 'text', m.content))
    end,
    attachments = coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'filename', a.filename,
          'mediaType', a.content_type,
          'url', a.public_url,
          'objectKey', a.object_key
        )
        order by a.created_at
      )
      from public.chat_attachments a
      where a.message_id = m.id
    ), '[]'::jsonb)
where m.parts = '[]'::jsonb;

-- Sanity: at least one part should exist after backfill for any message that
-- previously had non-empty content or attachments. We keep `content` for one
-- release as a denormalized mirror and drop chat_attachments in a follow-up
-- migration once frontend/backend cut-over has baked.
