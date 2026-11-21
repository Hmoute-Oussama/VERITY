# Incident recovery

## Evidence database damaged

1. Stop writes and MCP processes.
2. Run `verity diagnostics --json`.
3. Run `sqlite3 .verity/verity.db "PRAGMA integrity_check;"`.
4. Restore from a verified backup:

```bash
verity backup --out evidence-backup.db
verity restore evidence-backup.db --force
```

5. Re-run `verity verify --stale`.
6. Export a signed bundle after verification.

## Suspicious evidence

- Re-run `verity status --json`.
- Inspect `verity evidence --history`.
- Export and verify evidence bundles.
- Invalidate stale evidence and re-run verification.
- Preserve the original database and audit export before repair.

## Suspected secret exposure

1. Revoke the exposed credential.
2. Preserve sanitized logs only.
3. Revoke evidence bundles containing the secret.
4. Re-run secret scan.
5. Revoke signing keys if private signing material was exposed.
