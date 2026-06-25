-- Allow specialization_pct to be null when the athlete's preferred intensity
-- split has not yet been determined. The value is only meaningful once the
-- coaching agent has gathered enough context to recommend one; treating an
-- unknown value as the 80/20 default was misleading.
--
-- Applied to the preview DB on 2026-06-24 to stop a NOT NULL constraint
-- violation triggered when the agent called update_athlete_profile without
-- a known value for this field. This file codifies that change so local and
-- production environments converge on the same schema.
ALTER TABLE athlete_profiles ALTER COLUMN specialization_pct DROP NOT NULL;
ALTER TABLE athlete_profiles ALTER COLUMN specialization_pct DROP DEFAULT;
