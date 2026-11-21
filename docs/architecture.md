# Architecture

```text
Claims + Git diff
        |
        v
Claim extractor  ->  deduplicated SQLite claims
        |
        v
Verifier registry
        |
        v
Evidence record -> dependency digests -> invalidation
        |
        +--> CLI
        +--> MCP
        +--> HTTP dashboard
        +--> evidence snapshots and graphs
```

## Design rules

- Agent statements are claims, never evidence.
- Verifiers are independent and explainable.
- Evidence is stored locally in SQLite.
- Evidence is invalidated from dependency changes.
- Command execution is conservative and allowlist-oriented.
- MCP and HTTP surfaces expose the same repository engine.

## Core modules

| Module | Responsibility |
|---|---|
| `claims/` | Deterministic claim extraction |
| `evidence/` | Independent verifiers |
| `engine.py` | Analysis, verification, and invalidation |
| `db.py` | SQLite persistence |
| `guard.py` | Action prerequisites |
| `contracts.py` | Completion requirements |
| `integrations.py` | Agent configuration writers |
| `server/mcp.py` | MCP stdio protocol |
