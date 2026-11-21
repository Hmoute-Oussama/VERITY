# VERITY 1.0 release checklist

## Automated evidence

- [ ] 12-platform test matrix is green.
- [ ] Package build and Twine checks pass.
- [ ] Clean wheel installation passes.
- [ ] MCP conformance tests pass.
- [ ] Signing, key rotation, and trusted verification pass.
- [ ] Backup, restore, diagnostics, and audit chain pass.
- [ ] Runtime/deployment verifiers pass fixtures.
- [ ] Plugin registry tests pass.
- [ ] CodeQL, dependency review, and adversarial security workflows pass.
- [ ] JSON schemas and YAML validation pass.

## External evidence

- [ ] Third-party security report is published and recorded.
- [ ] Auditor retest is passed.
- [ ] Live Claude Code session is recorded.
- [ ] Live Codex session is recorded.
- [ ] Live OpenCode session is recorded.
- [ ] Live Cursor session is recorded.
- [ ] Live VS Code/Copilot session is recorded.
- [ ] Live Windsurf session is recorded.
- [ ] Live Gemini CLI session is recorded.

## Public repository evidence

- [ ] Eleven public example repositories are published.
- [ ] Dependency-change repository is no longer pending.
- [ ] Public repositories include CI and pre-commit hooks.
- [ ] Public repository links match `public-repositories.json`.

## Signing and provenance

- [ ] Release key is recorded in a trusted key policy.
- [ ] Wheel and source archive hashes are recorded.
- [ ] GitHub attestations are present.
- [ ] PyPI trusted publishing succeeds.
- [ ] GitHub release assets match package files.

## Release command

```bash
verity release-evidence --out release-evidence.json
git tag v1.0.0
git push origin v1.0.0
```
