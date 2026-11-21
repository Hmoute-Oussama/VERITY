# VERITY 1.0 stability commitment

VERITY 1.0 is a compatibility commitment, not merely a version label.

## Public 1.0 contracts

The following are stable:

- `verity` CLI command names and documented options.
- Exit codes `0`, `1`, and `2`.
- JSON envelope `verity.output.v1`.
- MCP tool names and resources.
- Evidence statuses and overall statuses.
- Evidence bundle `verity.evidence-bundle.v1`.
- Verifier IDs and existing argument meanings.
- SQLite forward migrations.
- Versioned JSON Schema files under `schemas/`.

## Compatibility rules

Backward-compatible changes may be made in any release:

- Add optional CLI flags.
- Add optional JSON fields.
- Add tools, resources, verifiers, or templates.
- Add optional YAML fields.
- Improve summaries and diagnostics.

The following are breaking and require a major release:

- Remove or rename a documented command or option.
- Change an existing JSON field type or meaning.
- Remove or rename a verifier or MCP tool.
- Change existing verifier argument semantics.
- Change exit-code semantics.
- Change evidence or overall status meanings.
- Change evidence database compatibility without migration.

## Deprecation

Deprecated behavior remains usable for at least one minor release series.
Deprecations document replacement behavior, timeline, and migration commands.
