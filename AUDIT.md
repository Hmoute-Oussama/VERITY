# Independent security review

VERITY does not claim an external security audit until a qualified third-party
review is published and linked here.

## Required review areas

1. MCP JSON-RPC parsing and stdio framing.
2. Command allowlisting and shell/path escaping.
3. Container and environment sandbox boundaries.
4. Agent configuration merging.
5. SQLite migration and evidence integrity.
6. Secret scanning and redaction boundaries.
7. Evidence bundle canonicalization and Ed25519 verification.
8. Dependency, plugin, and supply-chain trust.
9. Cross-platform filesystem and symlink behavior.
10. Backup, restore, and diagnostics disclosure.

## External report intake

Approved reports should include:

- Auditor name and scope.
- VERITY version and commit.
- Testing environment and date.
- Findings and severity.
- Remediation commit IDs.
- Retest evidence.
- Public or controlled-disclosure status.

Do not present a CodeQL, Dependabot, or unit-test result as an independent
security audit.

## Repository status

External audit status is stored in `.verity/security-audits.json` and surfaced
with:

```bash
verity evidence-status --json
```

Record a report only after the auditor and report URL are known:

```bash
verity evidence-record audit --name "Auditor Name" --version "1.0.0" --commit "<commit>" --url "https://..." --result "retest-passed"
```
