"""One-command integration with AI coding agents.

VERITY talks to agents through MCP. This module knows where each agent expects
its MCP configuration and how to write it without clobbering existing settings.

Supported today:

* Claude Code (``claude``)
* OpenAI Codex CLI (``codex``)
* OpenCode (``opencode``)
* Cursor (``cursor``)
* GitHub Copilot in VS Code (``vscode``)
* Windsurf (``windsurf``)
* Gemini CLI (``gemini``)
* a generic JSON snippet (``generic``)

The writer is idempotent: running it twice produces one ``verity`` entry and one
managed instruction block, never duplicates.
"""

from __future__ import annotations

import json
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .util import load_json

MANAGED_START = "verity:start"
MANAGED_END = "verity:end"
MARKDOWN_START = f"<!-- {MANAGED_START} -->"
MARKDOWN_END = f"<!-- {MANAGED_END} -->"
TOML_START = f"# {MANAGED_START} - managed by `verity integrate`"
TOML_END = f"# {MANAGED_END}"


class IntegrationError(RuntimeError):
    """Raised when an existing configuration cannot be merged safely."""


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    description: str
    docs: str
    format: str  # json | toml | print
    project_path: str | None = None
    user_path: str | None = None
    default_scope: str = "project"  # project | user
    instructions: str | None = None
    server_key: tuple[str, ...] = ("mcpServers", "verity")
    schema_url: str | None = None
    supports_hooks: bool = False


TARGETS: tuple[Target, ...] = (
    Target(
        id="claude",
        name="Claude Code",
        description="Project-scoped MCP server plus CLAUDE.md rules.",
        docs="https://docs.anthropic.com/en/docs/claude-code/mcp",
        format="json",
        project_path=".mcp.json",
        user_path=".claude.json",
        default_scope="project",
        instructions="CLAUDE.md",
        server_key=("mcpServers", "verity"),
        supports_hooks=True,
    ),
    Target(
        id="codex",
        name="OpenAI Codex CLI",
        description="MCP server in Codex config.toml plus AGENTS.md rules.",
        docs="https://developers.openai.com/codex/mcp/",
        format="toml",
        project_path=".codex/config.toml",
        user_path=".codex/config.toml",
        default_scope="user",
        instructions="AGENTS.md",
    ),
    Target(
        id="opencode",
        name="OpenCode",
        description="Local MCP server in opencode.json plus AGENTS.md rules.",
        docs="https://opencode.ai/docs/mcp-servers/",
        format="json",
        project_path="opencode.json",
        user_path=".config/opencode/opencode.json",
        default_scope="project",
        instructions="AGENTS.md",
        server_key=("mcp", "servers", "verity"),
        schema_url="https://opencode.ai/config.json",
    ),
    Target(
        id="cursor",
        name="Cursor",
        description="Project MCP server plus a Cursor rule.",
        docs="https://docs.cursor.com/context/model-context-protocol",
        format="json",
        project_path=".cursor/mcp.json",
        user_path=".cursor/mcp.json",
        default_scope="project",
        instructions=".cursor/rules/verity.mdc",
        server_key=("mcpServers", "verity"),
    ),
    Target(
        id="vscode",
        name="GitHub Copilot (VS Code)",
        description="Workspace MCP server plus Copilot instructions.",
        docs="https://code.visualstudio.com/docs/copilot/chat/mcp-servers",
        format="json",
        project_path=".vscode/mcp.json",
        default_scope="project",
        instructions=".github/copilot-instructions.md",
        server_key=("servers", "verity"),
    ),
    Target(
        id="windsurf",
        name="Windsurf",
        description="User-level MCP server plus AGENTS.md rules.",
        docs="https://docs.windsurf.com/windsurf/cascade/mcp",
        format="json",
        user_path=".codeium/windsurf/mcp_config.json",
        default_scope="user",
        instructions="AGENTS.md",
        server_key=("mcpServers", "verity"),
    ),
    Target(
        id="gemini",
        name="Gemini CLI",
        description="User-level MCP server plus GEMINI.md rules.",
        docs="https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md",
        format="json",
        user_path=".gemini/settings.json",
        default_scope="user",
        instructions="GEMINI.md",
        server_key=("mcpServers", "verity"),
    ),
    Target(
        id="generic",
        name="Generic MCP client",
        description="Print a portable stdio MCP JSON snippet.",
        docs="https://modelcontextprotocol.io/",
        format="print",
        default_scope="project",
    ),
)

TARGET_BY_ID = {target.id: target for target in TARGETS}


def target_ids() -> list[str]:
    return [target.id for target in TARGETS]


def get_target(target_id: str) -> Target:
    try:
        return TARGET_BY_ID[target_id]
    except KeyError as exc:
        raise IntegrationError(
            f"Unknown target `{target_id}`. Choose one of: {', '.join(target_ids())}."
        ) from exc


# ---------------------------------------------------------------------------
# server specification
# ---------------------------------------------------------------------------


def command_parts(command: str) -> list[str]:
    """Split a command string without mangling Windows paths."""
    command = command.strip()
    if not command:
        raise IntegrationError("--command cannot be empty")
    if " " not in command and "\t" not in command:
        return [command]
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    return [part.strip('"') for part in parts if part.strip('"')]


def server_spec(
    root: Path,
    *,
    command: str = "verity",
    use_python: bool = False,
    absolute_root: bool = False,
) -> tuple[str, list[str]]:
    """Return (executable, args) for the VERITY stdio server."""
    if use_python:
        executable, args = sys.executable, ["-m", "verity"]
    else:
        parts = command_parts(command)
        executable, args = parts[0], parts[1:]
    args = [*args, "mcp"]
    if absolute_root:
        args.extend(["--root", str(Path(root).resolve())])
    return executable, args


def _payload(target: Target, executable: str, args: list[str]) -> dict[str, Any]:
    if target.id == "opencode":
        # OpenCode V2 nests servers under mcp.servers and uses `disabled`
        # rather than `enabled`; omitting it keeps the server enabled.
        return {"type": "local", "command": [executable, *args]}
    if target.id == "vscode":
        return {"type": "stdio", "command": executable, "args": args}
    return {"command": executable, "args": args}


# ---------------------------------------------------------------------------
# writers
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _nested_set(data: dict[str, Any], keys: tuple[str, ...], value: Any) -> None:
    node = data
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[keys[-1]] = value


def _merge_json(
    path: Path,
    keys: tuple[str, ...],
    value: Any,
    *,
    force: bool = False,
    defaults: dict[str, Any] | None = None,
) -> str:
    """Merge one nested key into a JSON file. Returns created|updated|unchanged."""
    existed = path.exists()
    data: dict[str, Any]
    if existed:
        try:
            loaded = load_json(path)
        except (OSError, ValueError) as exc:
            if not force:
                raise IntegrationError(
                    f"{path} is not valid JSON ({exc}). Re-run with --force to replace it."
                ) from exc
            loaded = {}
        if not isinstance(loaded, dict):
            if not force:
                raise IntegrationError(
                    f"{path} does not contain a JSON object. Re-run with --force to replace it."
                )
            loaded = {}
        data = loaded
    else:
        data = {}

    before = json.dumps(data, sort_keys=True)
    for key, default in (defaults or {}).items():
        data.setdefault(key, default)
    _nested_set(data, keys, value)
    after = json.dumps(data, sort_keys=True)
    if existed and before == after:
        return "unchanged"
    _write_text(path, json.dumps(data, indent=2) + "\n")
    return "updated" if existed else "created"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise IntegrationError(f"Cannot encode {type(value).__name__} as TOML")


def _strip_toml_table(text: str, table: str) -> str:
    """Remove one top-level TOML table, preserving everything around it."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    header = f"[{table}]"
    for line in lines:
        stripped = line.strip()
        if stripped == header or stripped.startswith(header[:-1] + "."):
            skipping = True
            continue
        if skipping:
            if stripped.startswith("[") and not stripped.startswith("[["):
                skipping = False
            elif stripped.startswith("[["):
                skipping = False
            else:
                continue
        out.append(line)
    return "".join(out)


def _merge_toml_block(
    path: Path,
    table: str,
    values: dict[str, Any],
    *,
    force: bool = False,
) -> str:
    """Insert or replace a managed TOML block, preserving the rest of the file."""
    existed = path.exists()
    text = _read_text(path)
    lines = [TOML_START, f"[{table}]"]
    for key, value in values.items():
        lines.append(f"{key} = {_toml_value(value)}")
    lines.append(TOML_END)
    block = "\n".join(lines)

    if TOML_START in text and TOML_END in text:
        start = text.index(TOML_START)
        end = text.index(TOML_END) + len(TOML_END)
        updated = text[:start] + block + text[end:]
        if updated == text:
            return "unchanged"
        _write_text(path, updated)
        return "updated"

    if not existed or not text.strip():
        _write_text(path, block + "\n")
        return "created"

    # An existing [mcp_servers.verity] table that we did not write would be
    # duplicated by appending a second table. Refuse rather than corrupt.
    if f"[{table}]" in text:
        if not force:
            raise IntegrationError(
                f"{path} already contains a [{table}] table not managed by VERITY. "
                "Remove it or re-run with --force to replace it."
            )
        text = _strip_toml_table(text, table)
    separator = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    _write_text(path, text + separator + block + "\n")
    return "updated"


def _merge_claude_hook(
    path: Path,
    executable: str,
    args: list[str],
    *,
    force: bool = False,
) -> str:
    """Install the PreToolUse guard hook without disturbing other hooks."""
    existed = path.exists()
    data: dict[str, Any] = {}
    if existed:
        loaded = load_json(path)
        if isinstance(loaded, dict):
            data = loaded
        elif not force:
            raise IntegrationError(
                f"{path} is not a JSON object. Re-run with --force to replace it."
            )

    desired = {
        "type": "command",
        "command": executable,
        "args": args,
        "timeout": 30,
    }
    before = json.dumps(data, sort_keys=True)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        if not force:
            raise IntegrationError(f"{path}: `hooks` is not an object.")
        hooks = {}
        data["hooks"] = hooks
    pre = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre, list):
        if not force:
            raise IntegrationError(f"{path}: `hooks.PreToolUse` is not an array.")
        pre = []
        hooks["PreToolUse"] = pre

    replaced = False
    for group in pre:
        if not isinstance(group, dict):
            continue
        for handler in group.get("hooks", []) or []:
            if not isinstance(handler, dict):
                continue
            blob = " ".join(
                [str(handler.get("command", "")), *[str(a) for a in handler.get("args", []) or []]]
            )
            if "verity" in blob and "hook" in blob and "guard" in blob:
                handler.clear()
                handler.update(desired)
                replaced = True
                break
        if replaced:
            break
    if not replaced:
        pre.append({"matcher": "Bash|PowerShell", "hooks": [desired]})

    after = json.dumps(data, sort_keys=True)
    if existed and before == after:
        return "unchanged"
    _write_text(path, json.dumps(data, indent=2) + "\n")
    return "updated" if existed else "created"


def _merge_markdown_block(path: Path, content: str, *, frontmatter: bool = False) -> str:
    existed = path.exists()
    text = _read_text(path)
    block = f"{MARKDOWN_START}\n{content.strip()}\n{MARKDOWN_END}"

    if MARKDOWN_START in text and MARKDOWN_END in text:
        start = text.index(MARKDOWN_START)
        end = text.index(MARKDOWN_END) + len(MARKDOWN_END)
        updated = text[:start] + block + text[end:]
        if updated == text:
            return "unchanged"
        _write_text(path, updated)
        return "updated"

    if not existed:
        header = ""
        if frontmatter:
            header = (
                "---\n"
                "description: VERITY evidence rules for AI agents\n"
                "alwaysApply: true\n"
                "---\n\n"
            )
        _write_text(path, header + block + "\n")
        return "created"

    separator = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    _write_text(path, text + separator + block + "\n")
    return "updated"


# ---------------------------------------------------------------------------
# instructions
# ---------------------------------------------------------------------------


def instruction_block() -> str:
    return """\
## VERITY: no claim without evidence

This project uses VERITY as its reality layer. Treat it as the source of truth
about what is actually implemented, installed, configured and tested.

**Required protocol**

1. Before planning or editing, call `verity_reality` (or run `verity status`).
2. Before running a risky shell command, call `verity_check_action` with the
   exact command. If it returns `block`, do not run the command; fix the missing
   prerequisite first.
3. Before saying work is complete, call `verity_verify_claim` with your
   completion statement and call `verity_contract_check`. Only report done when
   the contract check passes.
4. Never present your own statement as evidence. "Tests pass" requires
   `test_execution` evidence; "the endpoint exists" requires `code_route`
   evidence.
5. If evidence is stale or contradicted, say so and re-run `verity_verify`.
   Do not describe stale evidence as current.

**CLI fallback when MCP is unavailable**

```sh
verity status --json
verity analyze "<your claim>" --diff
verity guard "<command>" --json     # exit 2 = blocked
verity contract check --json
```

MCP tools: `verity_reality`, `verity_check_action`, `verity_verify_claim`,
`verity_list_claims`, `verity_verify`, `verity_scan`, `verity_contract_check`.
"""


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


@dataclass
class Change:
    target: str
    kind: str  # config | instructions | print
    path: Path | None
    status: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "kind": self.kind,
            "path": str(self.path) if self.path else None,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class InstallResult:
    target: str
    scope: str
    changes: list[Change] = field(default_factory=list)
    config: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "scope": self.scope,
            "changes": [change.to_dict() for change in self.changes],
            "config": self.config,
        }


def _config_path(target: Target, root: Path, home: Path, scope: str) -> Path | None:
    if scope == "user":
        if not target.user_path:
            raise IntegrationError(
                f"{target.name} has no user-level MCP config; use --project instead."
            )
        return home / target.user_path
    if not target.project_path:
        raise IntegrationError(
            f"{target.name} has no project-level MCP config; use --global instead."
        )
    return root / target.project_path


def install(
    target_id: str,
    root: Path,
    *,
    scope: str | None = None,
    command: str = "verity",
    use_python: bool = False,
    with_instructions: bool = True,
    with_hooks: bool = False,
    force: bool = False,
    home: Path | None = None,
    print_only: bool = False,
) -> InstallResult:
    target = get_target(target_id)
    root = Path(root).resolve()
    home = Path(home).expanduser().resolve() if home else Path.home()
    scope = scope or target.default_scope
    if scope not in {"project", "user"}:
        raise IntegrationError("scope must be 'project' or 'user'")

    absolute_root = scope == "user"
    executable, args = server_spec(
        root, command=command, use_python=use_python, absolute_root=absolute_root
    )
    payload = _payload(target, executable, args)
    if target.id == "claude" and scope == "project" and not print_only:
        # Claude Code sets CLAUDE_PROJECT_DIR for stdio servers. Using it makes
        # the committed .mcp.json portable across checkouts.
        payload = dict(payload)
        payload["args"] = [*payload["args"], "--root", "${CLAUDE_PROJECT_DIR:-.}"]
    result = InstallResult(target=target.id, scope=scope, config=payload)

    if print_only or target.format == "print":
        if target.id == "generic":
            snippet = {"mcpServers": {"verity": payload}}
        else:
            snippet = {target.server_key[0]: {target.server_key[1]: payload}}
        result.changes.append(
            Change(target=target.id, kind="print", path=None, status="printed", detail=json.dumps(snippet, indent=2))
        )
        result.config = snippet
        return result

    path = _config_path(target, root, home, scope)
    assert path is not None
    if target.format == "toml":
        status = _merge_toml_block(
            path,
            "mcp_servers.verity",
            {"command": executable, "args": args},
            force=force,
        )
    else:
        status = _merge_json(
            path,
            target.server_key,
            payload,
            force=force,
            defaults={"$schema": target.schema_url} if target.schema_url else None,
        )
    result.changes.append(
        Change(target=target.id, kind="config", path=path, status=status)
    )

    if with_instructions and target.instructions:
        instructions_path = root / target.instructions
        status = _merge_markdown_block(
            instructions_path,
            instruction_block(),
            frontmatter=target.instructions.endswith(".mdc"),
        )
        result.changes.append(
            Change(
                target=target.id,
                kind="instructions",
                path=instructions_path,
                status=status,
            )
        )

    if with_hooks and target.supports_hooks:
        if use_python:
            hook_executable, hook_args = sys.executable, ["-m", "verity", "hook", "guard"]
        else:
            parts = command_parts(command)
            hook_executable, hook_args = parts[0], [*parts[1:], "hook", "guard"]
        settings_path = (home if scope == "user" else root) / ".claude" / "settings.json"
        status = _merge_claude_hook(
            settings_path, hook_executable, hook_args, force=force
        )
        result.changes.append(
            Change(
                target=target.id,
                kind="hook",
                path=settings_path,
                status=status,
            )
        )
    return result


def install_many(
    target_ids_: Iterable[str],
    root: Path,
    **kwargs: Any,
) -> list[InstallResult]:
    results: list[InstallResult] = []
    for target_id in target_ids_:
        results.append(install(target_id, root, **kwargs))
    return results
