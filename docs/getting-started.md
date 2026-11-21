# Getting started

## Install

Preview PyPI installation:

```bash
pipx install verity-evidence
```

Development checkout:

```bash
git clone https://github.com/Hmoute-Oussama/VERITY.git
cd VERITY
python -m pip install -e ".[dev]"
```

## Initialize

Run VERITY in the project to protect:

```bash
cd /path/to/project
verity init
verity doctor
```

This creates `.verity/config.yaml`, `.verity/contracts.yaml`, and a local
`.verity/verity.db` evidence database.

## First verification

```bash
verity analyze "The implementation is ready and tests pass" --diff
verity verify
verity status
```

Use `verity status --json` for automation.

## Completion contract

Edit `.verity/contracts.yaml`, then:

```bash
verity contract check
```

An unmet required contract returns a non-zero exit code.

## Add an agent

```bash
verity integrate claude
verity integrate codex
verity integrate opencode
verity integrate cursor
verity integrate vscode
```

See [Agent integrations](integrations.md).
