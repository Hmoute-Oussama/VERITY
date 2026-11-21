# Security policy

## Supported versions

VERITY is currently maintained on the `main` branch. Security fixes are released
on a best-effort basis until a formal support policy is published.

## Reporting a vulnerability

Do **not** open a public issue for a vulnerability. Use GitHub's private
vulnerability reporting feature:

https://github.com/Hmoute-Oussama/VERITY/security/advisories/new

Include:

- A clear description and impact.
- Reproduction steps or a minimal example.
- Affected platforms and VERITY versions.
- Suggested mitigations where applicable.
- Evidence such as logs or a redacted proof of concept.

Please allow reasonable time for triage and coordinated disclosure.

## Security boundaries

- VERITY is deterministic and does not use an LLM for verification.
- Command execution is disabled or allowlisted by default.
- HTTP probes are restricted to the configured host allowlist.
- Evidence databases are local and should be reviewed as potentially sensitive.
- Agent configuration writers merge but do not validate a target repository.
- Imported project files are untrusted input.

Do not run VERITY against an untrusted repository without reviewing scripts,
verifiers, configuration, and hook behavior in that environment.
