# Evidence contracts

Contracts are machine-readable definitions of what must be proven before work
is complete. They live in `.verity/contracts.yaml`.

```yaml
version: 1
task: release
contracts:
  - id: tests_pass
    claim: The test suite passes.
    verifier: test_execution
    required: true
```

Each contract has:

| Field | Required | Meaning |
|---|---:|---|
| `id` | yes | Stable contract identifier |
| `claim` | yes | Falsifiable project claim |
| `verifier` | yes | Independent verification strategy |
| `args` | no | Verifier-specific arguments |
| `required` | no | Whether release depends on this contract |
| `description` | no | Human-readable context |

Run:

```bash
verity contract init
verity contract list
verity contract check --json
```

The JSON schema is available at
`schemas/contracts.schema.json`.
