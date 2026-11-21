# World-class Quickstart

## Install

```bash
pipx install verity-evidence
verity --version --json
```

## Initialize

```bash
cd /path/to/project
verity init --template python
verity doctor --json
```

Templates: `general`, `python`, `javascript`, `docker`, and `monorepo`.

## Verify

```bash
verity analyze "Implementation complete and tests pass" --diff
verity verify --json
verity status --json
verity contract check --json
```

## Export evidence

```bash
verity export --out evidence.json
verity export --format junit --out results.xml
verity export --format sarif --out results.sarif
```

## Signing

```bash
verity keygen
verity export --private-key .verity/evidence-signing-private.pem --out evidence.json
verity verify-bundle evidence.json .verity/evidence-signing-public.pem
verity trust add --name release --public-key .verity/evidence-signing-public.pem
verity verify-bundle evidence.json .verity/evidence-signing-public.pem --trusted-only
```

## Recovery

```bash
verity backup --out evidence-backup.db
verity restore evidence-backup.db --force
verity diagnostics --json
verity audit --out audit.json
```
