# Reproducible environments

Every evidence bundle can include:

- Operating system and release
- CPU architecture
- Python runtime and executable
- Working directory
- CI status

For stronger reproducibility, record in CI:

- Runtime and toolchain versions
- Lockfile digests
- Container image digests
- Package registry URLs
- Environment variable names, never secret values
- Git commit and dirty-tree status
- Execution duration and timeout

Secrets must never be copied into evidence payloads or bundles.
