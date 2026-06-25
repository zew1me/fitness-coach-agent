# Remove Pending Coach Tools Design

## Context

The coach advertises fifteen tools to the model, but seven have no execution path and return a
`pending_implementation` object. Advertising unavailable actions lets the model promise or display
work that the product cannot perform.

## Behavior

Only the eight tools with real execution paths remain in `coachToolDefinitions`:

- `get_athlete_context`
- `get_recent_activities`
- `get_active_plan`
- `process_uploaded_file`
- `update_athlete_profile`
- `calculate_zones`
- `estimate_thresholds`
- `generate_training_plan`

The seven unavailable tools are removed from the model-visible registry and from specialist proposed
write-tool validation. Their unused input schemas are removed. The generic
`pending_implementation` fallback becomes an explicit unknown-tool error so future registry drift
fails during development instead of being presented as a successful tool result.

## Issue Tracking

Each unavailable tool must have one implementation issue before removal. Existing issue #209 tracks
`save_activity_from_text`; issues #232 through #237 track the remaining six tools.

## Tests

Tests will assert the exact eight-tool registry, assert that the seven unavailable names are absent,
and update specialist-report schema coverage to use still-supported write tools. Existing execution
tests for the eight supported tools remain unchanged.
