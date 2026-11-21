# VERITY

**The reality layer for AI coding agents.**

> No claim without evidence. No action without reality.

Coding agents are confident, fast, and frequently wrong about the state of your
project. They say "tests pass" without running them, "the endpoint exists"
because they wrote a route they never wired up, and "docker compose works" when
there is no compose file. VERITY sits between the agent and the software
environment and forces every claim and every risky action through project
evidence.

VERITY is local-first, deterministic, and needs no LLM. It works as a CLI, an
MCP server, and an HTTP dashboard, and connects to Claude Code, Codex, OpenCode,
Cursor, VS Code, Windsurf, and Gemini CLI in one command.

---

## Why

An agent's own statement must never count as evidence. VERITY turns an agent's
prose into independent claims, verifies each claim against the repository,
records what it found, and invalidates that evidence when the files it depended
on change.

- **Claim decomposition** — "I implemented JWT auth using PyJWT, added the login
  endpoint, and all tests pass" becomes four separate claims, each with its own
  verifier.
- **Independent verification** — symbols, routes, imports, dependencies,
  configuration, schemas, tests, secrets, environment, HTTP probes.
- **Evidence decay** — every result records content hashes of the files it read.
  Edit one of those files and the evidence becomes `stale`; it is never silently
  reused.
- **Hallucination firewall** — commands whose prerequisites are demonstrably
  missing are blocked before they run.
- **Evidence contracts** — a YAML contract states what must be proven before a
  task may be called done.
- **Reality injection** — a compact, current snapshot is available to the agent
  through MCP tools and resources.

---

## Quickstart

```sh
git clone <your-fork> verity
cd verity
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -e .

cd ../my-project
verity init
verity integrate --all          # connect your coding agents over MCP
```

Then, from the agent or the terminal:

```sh
verity analyze "I implemented JWT authentication and all tests pass" --diff
verity verify
verity status
verity contract check
```

`verity init` creates:

```
.verity/config.yaml      # committed
.verity/contracts.yaml   # committed: what must be proven
.verity/verity.db        # local evidence store (gitignored)
```

---

## Connect your agent in one command

```sh
verity integrate claude
verity integrate codex
verity integrate opencode
verity integrate --all
```

`integrate` merges into existing configuration — it never replaces unrelated
settings — and is idempotent. It writes the agent's MCP config plus a short
evidence protocol into the agent's instruction file.

| Agent | Command | Writes |
| --- | --- | --- |
| Claude Code | `verity integrate claude` | `.mcp.json`, `CLAUDE.md` |
| Claude Code + hooks | `verity integrate claude --hooks` | also `.claude/settings.json` |
| OpenAI Codex CLI | `verity integrate codex` | `~/.codex/config.toml`, `AGENTS.md` |
| OpenAI Codex (project) | `verity integrate codex --project` | `.codex/config.toml`, `AGENTS.md` |
| OpenCode | `verity integrate opencode` | `opencode.json`, `AGENTS.md` |
| Cursor | `verity integrate cursor` | `.cursor/mcp.json`, `.cursor/rules/verity.mdc` |
| GitHub Copilot (VS Code) | `verity integrate vscode` | `.vscode/mcp.json`, `.github/copilot-instructions.md` |
| Windsurf | `verity integrate windsurf` | `~/.codeium/windsurf/mcp_config.json`, `AGENTS.md` |
| Gemini CLI | `verity integrate gemini` | `~/.gemini/settings.json`, `GEMINI.md` |
| Anything else | `verity integrate generic --print` | stdout |

Useful flags:

```sh
verity integrate --list                 # show agents, scopes and files
verity integrate claude --print         # print config, write nothing
verity integrate claude --project       # force project scope
verity integrate codex --global         # force user scope
verity integrate claude --python        # launch with: python -m verity mcp
verity integrate claude --no-instructions
verity integrate claude --hooks         # automatic PreToolUse guard
verity integrate claude --json          # machine-readable result
```

> **Source checkouts:** if `verity` is not on the agent's `PATH`, use
> `--python` so the config points at the interpreter that has VERITY installed.

### What the agent is told

The managed instruction block (safe to commit) requires the agent to:

1. call `verity_reality` before planning;
2. call `verity_check_action` before risky shell commands and obey `block`;
3. call `verity_verify_claim` and `verity_contract_check` before saying "done";
4. never present its own statement as evidence;
5. report stale or contradicted evidence instead of describing it as current.

### Automatic enforcement (Claude Code)

MCP tools are voluntary: an agent can forget to call them. `--hooks` installs a
`PreToolUse` hook that checks every Bash/PowerShell command against project
reality and returns a deny decision when a prerequisite is demonstrably missing.

```sh
verity integrate claude --hooks
```

The hook calls `verity hook guard`, which reads Claude Code's hook JSON from
stdin, resolves the project with `CLAUDE_PROJECT_DIR`, and blocks only when the
evidence is concrete. Uninitialized projects are allowed through.

You can test it without an agent:

```sh
echo '{"tool_name":"Bash","tool_input":{"command":"python missing_script.py"}}' \
  | verity hook guard
```

### Manual configuration

<details>
<summary>Claude Code — <code>.mcp.json</code></summary>

```json
{
  "mcpServers": {
    "verity": {
      "command": "verity",
      "args": ["mcp", "--root", "${CLAUDE_PROJECT_DIR:-.}"]
    }
  }
}
```
</details>

<details>
<summary>Codex CLI — <code>~/.codex/config.toml</code></summary>

```toml
[mcp_servers.verity]
command = "verity"
args = ["mcp", "--root", "/absolute/path/to/project"]
```
</details>

<details>
<summary>OpenCode — <code>opencode.json</code> (V2)</summary>

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
</details>

<details>
<summary>Cursor — <code>.cursor/mcp.json</code></summary>

```json
{
  "mcpServers": {
    "verity": { "command": "verity", "args": ["mcp"] }
  }
}
```
</details>

<details>
<summary>VS Code / Copilot — <code>.vscode/mcp.json</code></summary>

```json
{
  "servers": {
    "verity": {
      "type": "stdio",
      "command": "verity",
      "args": ["mcp"]
    }
  }
}
```
</details>

After writing config, restart the agent and check the server is connected
(`/mcp` in Claude Code, `codex mcp list`, `opencode mcp list`).

---

## The MCP surface

Tools:

| Tool | Purpose |
| --- | --- |
| `verity_reality` | Current verified / unverified / contradicted state |
| `verity_check_action` | Hallucination firewall for a shell command |
| `verity_verify_claim` | Decompose and verify an agent statement |
| `verity_list_claims` | Inspect claims and statuses |
| `verity_verify` | Re-run verification (optionally stale only) |
| `verity_scan` | Find unsupported assumptions in the working tree |
| `verity_contract_check` | Enforce evidence contracts before completion |

Resources: `verity://reality`, `verity://claims`, `verity://graph`.

Run the server directly with `verity mcp` (stdio JSON-RPC 2.0, no MCP SDK
required).

---

## Claims and evidence

A **claim** is a falsifiable statement about the project. A **claim draft** has a
text, a kind, and a verifier plus arguments. A **claim** is the stored,
deduplicated form. An **evidence record** is the result of running one verifier:
`pass`, `warn`, `fail`, `error`, or `stale`, with a summary, details, payload,
dependencies, confidence, and a timestamp.

Claims move through these statuses:

| Status | Meaning |
| --- | --- |
| `verified` | Latest evidence passes and nothing it depends on changed |
| `partially_verified` | Some evidence passes, some warns |
| `unverified` | No evidence yet, or evidence is inconclusive |
| `contradicted` | Evidence directly disproves the claim |
| `stale` | The files the evidence read have changed since verification |

### Evidence decay

Every evidence record stores SHA-256 content hashes of the files it depended on.
Before any status is reported, VERITY compares those hashes with the working
tree. Changed dependency → `stale`. Deleted dependency → `contradicted`/`stale`
depending on the verifier. Nothing is remembered as true after the reality it
described has moved.

```
verify app.py  ──►  evidence E1 (hash abc123)
edit app.py    ──►  status: stale
re-verify      ──►  evidence E2 (hash def456)
```

---

## Verifiers

| Verifier | Proves |
| --- | --- |
| `code_symbol` | A function/class/symbol exists in source (not just a comment) |
| `code_route` | An HTTP route exists, with optional method matching and fuzzy paths |
| `syntax` | Files parse as valid Python/JSON/YAML/TOML |
| `imports` | Imports resolve locally or are installed |
| `file_exists` | Paths exist (or are absent) |
| `config_valid` | Config files load and required keys are present |
| `json_path` | A JSON document contains a value at a path |
| `dependency` | A package is declared and available |
| `test_execution` | The allowlisted test command actually passes |
| `environment` | Language/runtime version, env var, or port state |
| `secret_scan` | No live credentials in the working tree (values redacted) |
| `security_analysis` | Heuristic checks for unsafe patterns |
| `database_schema` | A table/column exists in schema or migrations |
| `http_probe` | A local endpoint responds as expected |
| `command` | An allowlisted command exits successfully |
| `manual` | A human explicitly attested the claim |
| `none` | Records the claim as an explicit assumption, never as proof |

Execution is **allowlist-only by default** (`verification.allow_exec: false`).
Set it to `true` only in an environment where you accept arbitrary test commands.

---

## Evidence contracts

A contract is what must be proven before a task can be called done. Contracts
live in `.verity/contracts.yaml` and are committed.

```yaml
version: 1
task: login_feature

contracts:
  - id: tests_pass
    claim: The test suite passes on the current working tree.
    verifier: test_execution
    required: true

  - id: login_route
    claim: POST /login exists.
    verifier: code_route
    args:
      path: /login
      method: POST
    required: true

  - id: no_secrets
    claim: No secrets are committed.
    verifier: secret_scan
    required: true

  - id: jwt_dependency
    claim: PyJWT is declared and installed.
    verifier: dependency
    args:
      name: PyJWT
      candidates: [pyjwt]
    required: true
```

```sh
verity contract check        # exit 1 if any required contract is unsatisfied
```

Wire it into a git pre-commit hook with `verity hook install`.

---

## The hallucination firewall

```sh
verity guard "docker compose up --build"
```

```
VERDICT: BLOCK (risk: high)
ACTION: docker compose up --build
- compose file exists — No docker-compose.yml, compose.yaml or compose.yml
- Dockerfile exists — None of these paths exist: Dockerfile, Containerfile
```

Exit code `2` means blocked. `verity run "<command>"` checks first and executes
only if allowed.

The guard understands compose, Docker builds, npm/pnpm/yarn scripts, Maven,
Gradle, Go, Cargo, .NET, Alembic, pytest paths, Python/Node scripts, local
imports, and project policy (`deny_commands`, `allow_commands`, `strict`).

---

## CLI reference

| Command | What it does |
| --- | --- |
| `verity init` | Scaffold `.verity/` |
| `verity status` | Invalidate stale evidence, then report reality |
| `verity analyze "<text>" [--diff]` | Decompose prose/commands/diff into claims and verify |
| `verity verify [--stale-only]` | Re-run verifiers |
| `verity claim list/add/show/rm` | Manage claims |
| `verity evidence <claim-id>` | Show evidence history |
| `verity invalidate <claim-id>` | Force evidence to stale |
| `verity snapshot` | Markdown/JSON reality snapshot for agents |
| `verity graph` | Evidence graph (text, DOT, JSON) |
| `verity contract init/list/check` | Manage evidence contracts |
| `verity guard "<command>"` | Check a command before running it |
| `verity run "<command>"` | Check, then execute if allowed |
| `verity scan` | Find unsupported assumptions in the tree |
| `verity integrate <agent>` | Write MCP config and agent instructions |
| `verity mcp` | Run the stdio MCP server |
| `verity serve` | Local API + dashboard (`http://127.0.0.1:8787`) |
| `verity hook install` | Git pre-commit evidence gate |
| `verity hook guard` | PreToolUse guard for Claude Code |
| `verity doctor` | Check the installation |

Add `--json` to most commands for machine-readable output.

---

## Architecture

```
src/verity/
  cli.py                 Typer CLI, all commands
  engine.py              analyze / verify / invalidate / status orchestration
  models.py              claims, evidence, decisions, enums
  db.py                  SQLite schema and repository
  claims/extractor.py    deterministic claim decomposition
  evidence/              one verifier per claim kind
  decay.py               content-hash based invalidation
  guard.py               hallucination firewall and assumption scan
  contracts.py           evidence contracts
  integrations.py        agent config writers
  server/mcp.py          stdio MCP server
  server/http.py         local API + dashboard
  server/dashboard.html  vanilla JS dashboard
  snapshot.py            agent-facing reality context
  graph.py               claim → evidence → artifact graph
```

Design rules:

- **The agent is a source of claims, never a source of evidence.**
- **Deterministic by default.** Regex/AST extraction and real filesystem checks;
  no model decides what must be proven.
- **Local-first.** SQLite, stdlib HTTP, no telemetry, no network by default.
- **Explainable.** Every verdict names the exact file, command, or missing
  prerequisite behind it.
- **Conservative blocking.** The firewall blocks demonstrable contradictions, not
  uncertainty.
- **Evidence ages out.** If the dependency changed, the proof is gone.

---

## Development

```sh
pip install -e ".[dev]"
pytest
```

Python 3.10+. Dependencies: Typer, Rich, PyYAML. Everything else is stdlib.

---

## Roadmap

- Signed evidence bundles for CI and code review
- GitHub Action and PR evidence comments
- More verifiers: OpenAPI diffs, migrations vs. models, runtime traces
- OpenCode plugin and Codex post-run hook
- Team evidence store with shared invalidation
- VS Code extension and richer dashboard

---

## License

MIT. See [LICENSE](LICENSE).
