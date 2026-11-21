# VERITY governance

## Maintainers

Maintainers control the `main` branch, releases, security triage, and
compatibility decisions. Changes require tests and maintainer review.

## Decision process

- Bug fixes and documentation use normal pull requests.
- New verifiers or public CLI/MCP fields use the RFC process.
- Breaking changes require a major release and migration guide.
- Security fixes may be coordinated privately.

## RFCs

Proposal documents live in `docs/rfcs/` and include:

1. Motivation
2. Compatibility impact
3. Security and privacy impact
4. Implementation plan
5. Test and migration plan

## Releases

VERITY follows Semantic Versioning:

- Patch: bug and security fixes.
- Minor: backward-compatible features.
- Major: breaking CLI, MCP, JSON, contract, or database behavior.

## Security

Security reports are handled according to `SECURITY.md`. Maintainers target
acknowledgment within 3 working days and coordinated disclosure after a fix is
available or mitigated.

## Operational ownership

- One maintainer approves releases.
- One maintainer owns security triage and advisories.
- Breaking public API changes require an accepted RFC.
- Public compatibility claims require a passing CI run and, for agent claims,
  recorded agent-session evidence.

## Current project ownership

Until additional maintainers are formally appointed:

- Repository and release ownership: project owner.
- Security triage: project owner.
- Community triage: maintainers appointed through issues or discussions.
- External audit claims: no status is claimed without a published report.
- Live agent claims: no status is claimed without recorded session evidence.

## Community participation

Contributors may propose verifiers, documentation, integration fixtures,
security fixes, and agent compatibility reports. Public API changes require
tests, RFC review, migration documentation, and maintainer approval.
