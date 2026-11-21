# Changelog

All notable user-visible changes to VERITY will be documented in this file.

The format follows Keep a Changelog principles and release numbers follow
Semantic Versioning.

## [1.0.0] - 2026-11-21

### Added

- Stable public CLI and API version 1.0.
- Reproducible evidence bundles and SHA-256 integrity.
- Optional Ed25519 bundle signing and verification.
- JUnit XML and SARIF evidence exports.
- Sandboxed execution defaults and credential filtering.
- Deployment configuration verifier.
- Community plugin manifest discovery.
- Team metadata in evidence bundles.
- MCP conformance, adversarial, and signing tests.
- Governance and RFC documentation.
- Security workflows, MCP conformance matrix, backup/restore, audit export,
  diagnostics, signing key IDs, plugin trust metadata, and team policy fields.

## [Unreleased]

### Added

- Community support and security documentation.
- Cross-platform CI and release workflows.
- README project telemetry and animated visual assets.
- JSON schemas for VERITY configuration and evidence contracts.
- Versioned status, evidence, snapshot, graph, and MCP JSON schemas.
- Machine-readable API version metadata.
- Database schema migration tracking and forward-migration safeguards.
- 1.0 compatibility and database migration documentation.
- OpenAPI, migration consistency, lockfile consistency, and SBOM verifiers.
- Public verifier catalog and trust-model documentation.
- Versioned JSON output envelopes and canonical evidence bundle exports.
- MCP conformance tests for resources, tools, initialization, and malformed inputs.
- Project templates for Python, JavaScript, Docker, and monorepo onboarding.
- Four end-to-end project examples and formal 1.0 stability documentation.
- Marketplace-compatible root GitHub Action metadata.
- Public integration fixture matrix for FastAPI, Flask, Next.js, Docker Compose,
  monorepos, TypeScript, Java, Go, contradictions, stale evidence, and dependencies.

## [0.2.0] - 2026-11-21

### Added

- `verity-evidence` PyPI distribution name.
- First beta package metadata and trusted-Publishing release workflow.
- Configuration and contract validation commands.
- Cross-platform test matrix and package build workflow.

## [0.1.0] - 2026-11-21

### Added

- Claim extraction and evidence verification.
- Evidence content hashing and stale evidence invalidation.
- Hallucination firewall and project assumption scanning.
- Evidence contracts.
- SQLite evidence repository.
- MCP server and agent integrations.
- HTTP dashboard and CLI snapshots.
