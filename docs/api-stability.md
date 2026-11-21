# VERITY 1.0 compatibility contract

VERITY's beta-to-1.0 API is intentionally small and explicit.

## Stable surfaces

After 1.0:

- Existing CLI command names and options are preserved.
- Exit codes remain:
  - `0`: successful, verified, or allowed.
  - `1`: verification, evidence, contract, or repository failure.
  - `2`: invalid input or blocked action.
- `--json` uses the documented schema and never reports human formatting.
- MCP tool names and resource URIs are stable.
- Contract and verifier IDs are stable names; argument layouts are versioned.
- SQLite databases upgrade through forward migrations and are never silently
  rewritten to an incompatible format.
- JSON Schema files under `schemas/` are published with `/v1`-style intent.

## Adding behavior

The following changes are backward-compatible:

- Adding an optional field to JSON output.
- Adding a new verifier.
- Adding a new MCP tool.
- Adding a new resource.
- Adding an optional YAML contract field.
- Adding optional CLI flags.
- Adding an optional evidence dependency.

## Breaking changes

A breaking change requires a major release and a migration:

- Renaming or removing a CLI command or option.
- Changing an existing JSON field's type or meaning.
- Removing or renaming a MCP tool or resource.
- Changing verifier arguments incompatibly.
- Changing claim or evidence statuses.
- Changing exit-code semantics.
- Dropping a supported database migration path.

## Database migrations

Every evidence database has a `db_schema_version` metadata key. VERITY:

1. Creates missing metadata safely.
2. Applies forward migrations in order.
3. Records the resulting version.
4. Refuses to open an evidence database newer than the installed VERITY.
5. Never discards claims or evidence during an upgrade.
