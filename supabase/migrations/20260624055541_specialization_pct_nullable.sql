-- Make specialization_pct nullable so multi-sport athletes (duathletes, triathletes)
-- can have no single-sport specialization value.  A missing value is now stored as
-- NULL rather than defaulting to 80, which had no semantic meaning for athletes who
-- train across multiple disciplines.
--
-- The check constraint is preserved: when a value IS present it must be 0–100.
-- The DEFAULT is dropped because NULL is the correct initial state for new profiles
-- that the AI has not yet assessed.

ALTER TABLE public.athlete_profiles
    ALTER COLUMN specialization_pct DROP NOT NULL,
    ALTER COLUMN specialization_pct DROP DEFAULT;
