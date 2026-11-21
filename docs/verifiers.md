# Verifiers

VERITY verifiers convert claims into evidence. Agent prose is not evidence.

| Verifier | Evidence source |
|---|---|
| `code_symbol` | Python source symbol lookup |
| `code_route` | HTTP route declarations |
| `syntax` | Source/config parsing |
| `imports` | Local and installed import resolution |
| `file_exists` | Working-tree paths |
| `config_valid` | Configuration parsing and required keys |
| `json_path` | JSON value lookup |
| `dependency` | Package manifests and installed packages |
| `test_execution` | Allowlisted test command |
| `environment` | Runtime, environment variable, or port |
| `secret_scan` | Credential pattern and entropy checks |
| `security_analysis` | Heuristic unsafe-code checks |
| `database_schema` | Schema and migration declarations |
| `http_probe` | Restricted local HTTP endpoint |
| `command` | Allowlisted command execution |
| `manual` | Explicit human attestation |
| `none` | Assumption without proof |

Evidence statuses are `pass`, `warn`, `fail`, `error`, or `stale`.

Limitations:

- Static verifiers do not prove runtime behavior.
- Heuristic security scans are not formal audits.
- `manual` evidence is only as reliable as the attestor.
- Evidence dependency hashes detect source changes but cannot prove every
  external service remained unchanged.
