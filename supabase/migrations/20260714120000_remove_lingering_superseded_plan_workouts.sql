-- Remove lingering future scheduled workouts belonging to non-active plans.
--
-- One-time cleanup for pre-#312 superseded plans (issue #315). Before #312,
-- superseding a plan did not run `delete_future_scheduled_workouts`, so plans
-- flipped out of `status='active'` could keep future, `status='scheduled'`,
-- unmatched `plan_workouts` rows. The calendar read now scopes planned reads to
-- the active plan (see `_scope_planned_workouts_to_active_plan` in
-- `api/index.py`), so these rows no longer render, but this migration removes
-- the stale data at the source so it can never resurface via another read path.
--
-- Preview affected rows before applying:
-- select pw.id, pw.user_id, pw.plan_id, pw.workout_date, pw.status
-- from public.plan_workouts as pw
-- join public.training_plans as tp on tp.id = pw.plan_id
-- where pw.status = 'scheduled'
--   and pw.actual_activity_id is null
--   and pw.workout_date >= current_date
--   and tp.status <> 'active';
--
-- The DELETE below is scoped by exactly the same predicate as the read-time
-- filter and the `delete_future_scheduled_workouts` cleanup primitive: it only
-- touches future, `status='scheduled'`, unmatched rows owned by a non-active
-- (superseded/completed/draft) plan. Completed/matched/past-dated rows carry
-- history and are never removed. It is a no-op in an already-clean environment.
--
-- Rollout note: preview and production are separate Supabase projects; each must
-- be linked and `supabase db push` applied independently. This migration only
-- deletes rows the app no longer surfaces, so no maintenance window is required.
delete from public.plan_workouts as pw
using public.training_plans as tp
where pw.plan_id = tp.id
  and pw.status = 'scheduled'
  and pw.actual_activity_id is null
  and pw.workout_date >= current_date
  and tp.status <> 'active';
