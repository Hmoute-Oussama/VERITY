# Verifier trust model

VERITY verifiers are evidence-producing checks, not a claim of universal
correctness.

## Trust levels

| Evidence | Meaning |
|---|---|
| `pass` | Current evidence supports the claim |
| `warn` | Evidence is partial, inconclusive, or requires human review |
| `fail` | Evidence directly contradicts the claim or a required prerequisite |
| `error` | The verifier could not complete safely |
| `stale` | The evidence exists but a dependency changed |

## What is never inferred

- Agent prose is not evidence.
- A source file is not proof that code is deployed.
- A migration declaration is not proof that it was executed.
- A package manifest is not proof of runtime installation.
- A heuristic security scan is not a security audit.
- A lockfile is not proof that every package came from a trusted source.
- An SBOM is not proof that a package is vulnerability-free.

## Confidence

Every verifier result carries a confidence from `0.0` to `1.0`. Confidence is
metadata, not a probability guarantee. Required contracts use pass/fail status,
not confidence alone.

## Evidence dependencies

Every evidence result can name dependency files. VERITY records content digests
for them and invalidates the evidence when they change. External services and
runtime state should be represented by explicit timestamps or probes and should
not be described as permanent proof.
