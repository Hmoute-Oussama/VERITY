# Project onboarding

Initialize the right template:

```bash
verity init --template python
verity init --template javascript
verity init --template docker
verity init --template monorepo
```

## Python

- Add test execution and dependency contracts.
- Commit `.verity/config.yaml` and `.verity/contracts.yaml`.
- Run `verity status --json` in CI.

## JavaScript / TypeScript

- Verify `package.json` and lockfile consistency.
- Add source, test, and build contracts.
- Use `lockfile_consistency` for manifest drift.

## Docker

- Use `config_valid` for Compose.
- Use `verity guard "docker compose up --build"` before execution.
- Keep secrets outside evidence details.

## Monorepo

- Define package paths and package-specific required contracts.
- Prefer file and route contracts over repository-wide assumptions.
- Export evidence bundles for review.
