# Tutorial: verify a login feature

This example demonstrates the complete claim-to-evidence workflow.

## 1. Initialize

```bash
mkdir login-demo && cd login-demo
git init
verity init
```

## 2. Add completion contracts

Create `.verity/contracts.yaml`:

```yaml
version: 1
task: login-feature
contracts:
  - id: login_route
    claim: POST /login exists.
    verifier: code_route
    args:
      path: /login
      method: POST
    required: true
  - id: tests_pass
    claim: The test suite passes.
    verifier: test_execution
    required: true
  - id: no_secrets
    claim: No secrets are committed.
    verifier: secret_scan
    required: true
```

## 3. Implement and test

Implement `/login`, add tests, and run:

```bash
verity analyze "Login is implemented and tests pass" --diff
verity verify
verity contract check
```

## 4. Interpret the result

- `PASS` means current evidence supports the claim.
- `FAIL` means evidence directly contradicts it.
- `OPEN` means evidence is missing or inconclusive.
- `STALE` means a source dependency changed after verification.

Do not describe the feature as complete while a required contract remains open
or contradicted.

## 5. Guard execution

```bash
verity guard "docker compose up --build"
verity run "pytest"
```
