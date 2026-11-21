# MCP interoperability

MCP is tested through the stdio JSON-RPC server.

## Automated checks

```bash
python -m pytest tests/test_mcp.py
verity mcp
```

Tests cover initialize, ping, tools, resources, malformed arguments, unknown
tools, unknown resources, and unknown methods.

## Agent compatibility

| Agent | Scope | Status |
|---|---|---|
| Claude Code | MCP + optional hooks | Integration supported; session-tested evidence required |
| Codex | MCP | Integration supported; session-tested evidence required |
| OpenCode | MCP | Integration supported; session-tested evidence required |
| Cursor | MCP | Integration supported; session-tested evidence required |
| VS Code / Copilot | MCP | Integration supported; session-tested evidence required |
| Windsurf | MCP | Beta integration |
| Gemini CLI | MCP | Beta integration |

"Integration supported" does not mean externally audited in every agent release.
