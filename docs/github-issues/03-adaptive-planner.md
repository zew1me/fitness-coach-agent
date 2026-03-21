## Summary

Turn the planner from a static template into actual adaptation logic based on user signals.

## Current state

- `backend/services/planner.py` still returns nearly the same 14-day plan every time.
- Only tiny variations are triggered by image count or the word `travel`.

## Scope

- Model fatigue, schedule constraints, and goals more explicitly.
- Adjust volume, intensity, and recovery based on check-in inputs.
- Tighten alignment between prompt composition and returned plans.
- Add richer scenario-based tests.
