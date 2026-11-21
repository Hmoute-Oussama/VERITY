# Troubleshooting

## VERITY is not initialized

Run:

```bash
verity init
```

Set `--root /path/to/project` or run the command inside the project.

## No claims are found

The statement may not contain a currently supported claim pattern, or the diff
may be clean. Use `--diff`, `--transcript`, `--command`, or add a contract.

## Evidence is stale

Rerun verification:

```bash
verity verify --stale
```

The evidence dependency changed or its TTL expired.

## Tests fail in CI but pass locally

Compare:

- Python version.
- Operating system.
- Environment variables.
- Package versions.
- Working directory.
- Allowlisted test command.

## MCP server does not connect

1. Run `verity doctor`.
2. Run `verity mcp`.
3. Confirm the agent's MCP process can reach the executable.
4. Use `verity integrate <agent> --print` to inspect generated configuration.
5. Restart or reload the agent after configuration changes.
