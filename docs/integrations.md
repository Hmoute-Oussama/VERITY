# Agent integrations

VERITY exposes the same seven MCP tools to every agent. `verity integrate`
writes the agent-specific configuration and a shared evidence protocol.

```sh
verity integrate --list        # agents, default scopes, files
verity integrate --all         # all project/user defaults
verity integrate claude        # one agent
```

Every writer merges into existing configuration and is idempotent. Run it again
after moving a project or changing how VERITY is launched.

## Scope and portability

- **Project scope** is committed with the repository. The MCP server is launched
  with the project as its working directory, so no absolute path is needed.
- **User scope** is for one machine. The server is launched with
  `--root <absolute-project-path>`, so rerun `verity integrate <agent> --global`
  if the project moves.
- **Claude Code project config** uses `${CLAUDE_PROJECT_DIR:-.}`, which Claude
  sets for stdio servers, so `.mcp.json` stays portable across checkouts.

## Per-agent notes

### Claude Code

```sh
verity integrate claude            # .mcp.json + CLAUDE.md
verity integrate claude --hooks    # + .claude/settings.json PreToolUse guard
```

- Approve the project MCP server the first time you open the project (`/mcp`).
- Check status with `claude mcp list` and `/mcp`.
- `--hooks` makes the hallucination firewall automatic for Bash and PowerShell
  tool calls. Remove the `verity` handler from `.claude/settings.json` to disable
  it, or run `verity integrate claude` again without `--hooks` (the existing
  hook is left in place; delete it manually).
- The hook exits 0 with a `permissionDecision: "deny"` payload. Warnings are
  returned as a `systemMessage` and do not block.

### OpenAI Codex CLI

```sh
verity integrate codex             # ~/.codex/config.toml + AGENTS.md
verity integrate codex --project   # .codex/config.toml (trusted projects)
```

Check with `codex mcp list`. Codex shares MCP configuration between the CLI, IDE
extension, and desktop app for the same host.

### OpenCode

```sh
verity integrate opencode          # opencode.json + AGENTS.md
verity integrate opencode --global # ~/.config/opencode/opencode.json
```

VERITY writes the V2 layout:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "verity": {
        "type": "local",
        "command": ["verity", "mcp"]
      }
    }
  }
}
```

Check with `opencode mcp list` or `/mcps`.

### Cursor

```sh
verity integrate cursor            # .cursor/mcp.json + .cursor/rules/verity.mdc
```

Open **Settings → MCP** and confirm `verity` is connected.

### GitHub Copilot in VS Code

```sh
verity integrate vscode            # .vscode/mcp.json + Copilot instructions
```

Run **MCP: List Servers** from the command palette and start `verity`.

### Windsurf

```sh
verity integrate windsurf          # ~/.codeium/windsurf/mcp_config.json + AGENTS.md
```

Open Cascade → MCP and refresh.

### Gemini CLI

```sh
verity integrate gemini            # ~/.gemini/settings.json + GEMINI.md
```

Run `/mcp` inside Gemini CLI to confirm the server.

### Anything else

```sh
verity integrate generic --print
```

Prints a standard `mcpServers` snippet. Most clients accept it directly; VS Code
uses `servers` and OpenCode uses `mcp.servers`.

## When `verity` is not on PATH

Agents launch MCP servers in their own environment. If you run from a source
checkout or a virtualenv that the agent does not inherit, use:

```sh
verity integrate <agent> --python
```

This writes `python -m verity mcp` using the current interpreter. Make sure the
agent can run that interpreter and that VERITY is installed into it
(`pip install -e .`).

Alternatively pass an explicit executable:

```sh
verity integrate claude --command "/path/to/.venv/bin/verity"
```

## Verifying the connection

1. `verity doctor` — VERITY sees the project, database and git state.
2. Restart the agent.
3. Ask the agent: *"Call verity_reality and summarize the project's verified and
   unverified claims."*
4. If the tool is missing, check the agent's MCP log and confirm the command in
   its config resolves on `PATH`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Server exits immediately | Run the configured command manually in the project root; it should print a banner and wait on stdin |
| `VERITY is not initialized` | Run `verity init` in the project root |
| Tools missing in the agent | Restart the agent; approve project MCP servers if prompted |
| Hook never fires | Confirm the project is trusted; hooks do not run in untrusted folders |
| Hook blocks a legitimate command | Run `verity guard "<command>"` to see the evidence; fix the prerequisite or adjust `policy` in `.verity/config.yaml` |
| `python -m verity` fails in the agent | Install VERITY into that interpreter, or point `--command` at the right executable |
| Config was not written | Check the error; invalid JSON/TOML is never overwritten without `--force` |

## Security

- MCP config files contain commands, not secrets.
- The guard blocks only demonstrable contradictions; it does not execute the
  command it checks.
- Test execution stays allowlist-only until `verification.allow_exec: true`.
- Secret values found by `secret_scan` are redacted in evidence and output.
