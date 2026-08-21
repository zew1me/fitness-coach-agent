-- Atomically merge a goal's course-profile notes without replacing sibling keys.
--
-- A read-merge-write from the application could overwrite another concurrent
-- course_profile update. PostgreSQL evaluates this expression while holding the
-- row lock, so it always merges against the latest committed profile.
create or replace function public.update_goal_course_profile_notes_atomic(
  p_goal_id uuid,
  p_user_id text,
  p_notes text
)
returns public.goals
language plpgsql
security definer
set search_path = ''
as $$
declare
  updated_goal public.goals%rowtype;
begin
  if nullif(p_user_id, '') is null then
    raise exception 'user_id is required to update goal course profile notes'
      using errcode = '22023';
  end if;
  if p_notes is null then
    raise exception 'notes are required to update goal course profile notes'
      using errcode = '22023';
  end if;

  update public.goals
  set course_profile = coalesce(course_profile, '{}'::jsonb) || jsonb_build_object('notes', p_notes)
  where id = p_goal_id
    and user_id = p_user_id
  returning * into updated_goal;

  if not found then
    return null;
  end if;
  return updated_goal;
end;
$$;

revoke all on function public.update_goal_course_profile_notes_atomic(uuid, text, text)
from public, anon, authenticated;
grant execute on function public.update_goal_course_profile_notes_atomic(uuid, text, text)
to service_role;
