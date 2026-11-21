# Migration guide

## Upgrading VERITY

Before upgrading:

```bash
verity doctor --json
verity snapshot --json
cp .verity/verity.db .verity/verity.db.backup
```

Upgrade the package, then inspect compatibility:

```bash
pipx upgrade verity-evidence
verity --version
verity doctor --json
```

Forward database migrations are automatic. VERITY refuses databases created by
newer schema versions, with an upgrade hint rather than silent data loss.

## Breaking major releases

Major releases document:

- Removed commands or options.
- JSON and MCP output differences.
- Contract or verifier argument changes.
- Evidence database migration steps.
- Rollback procedures.

Never edit `.verity/verity.db` by hand as a migration strategy.
