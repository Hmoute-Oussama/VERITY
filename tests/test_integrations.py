from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from verity.integrations import IntegrationError, install, server_spec


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / ".verity").mkdir()
    return tmp_path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_claude_writes_mcp_config_and_instructions(root: Path, tmp_path: Path):
    result = install("claude", root, use_python=True, home=tmp_path / "home")

    data = read_json(root / ".mcp.json")
    spec = data["mcpServers"]["verity"]
    assert spec["args"][:3] == ["-m", "verity", "mcp"]
    assert spec["args"][-2:] == ["--root", "${CLAUDE_PROJECT_DIR:-.}"]
    assert spec["command"]

    instructions = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "verity_verify_claim" in instructions
    assert instructions.count("verity:start") == 1

    assert [c.kind for c in result.changes] == ["config", "instructions"]
    assert all(c.status == "created" for c in result.changes)


def test_integration_is_idempotent(root: Path, tmp_path: Path):
    install("claude", root, use_python=True, home=tmp_path / "home")
    second = install("claude", root, use_python=True, home=tmp_path / "home")

    data = read_json(root / ".mcp.json")
    assert list(data["mcpServers"]) == ["verity"]
    instructions = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert instructions.count("verity:start") == 1
    assert all(change.status == "unchanged" for change in second.changes)


def test_existing_json_keys_are_preserved(root: Path, tmp_path: Path):
    path = root / ".mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"other": {"command": "other"}}, "custom": True}),
        encoding="utf-8",
    )
    install("claude", root, use_python=True, home=tmp_path / "home", with_instructions=False)

    data = read_json(path)
    assert data["custom"] is True
    assert data["mcpServers"]["other"]["command"] == "other"
    assert "verity" in data["mcpServers"]


def test_codex_user_config_uses_absolute_root(root: Path, tmp_path: Path):
    home = tmp_path / "home"
    install("codex", root, use_python=True, home=home)

    text = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.verity]" in text
    parsed = tomllib.loads(text)
    assert str(root.resolve()) in parsed["mcp_servers"]["verity"]["args"]
    assert "verity:start" in text
    assert (root / "AGENTS.md").exists()


def test_codex_toml_is_idempotent(root: Path, tmp_path: Path):
    home = tmp_path / "home"
    install("codex", root, use_python=True, home=home)
    install("codex", root, use_python=True, home=home)

    text = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert text.count("[mcp_servers.verity]") == 1
    assert text.count("verity:start") == 1


def test_codex_force_replaces_unmanaged_table(root: Path, tmp_path: Path):
    home = tmp_path / "home"
    path = home / ".codex" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        '[mcp_servers.verity]\ncommand = "old"\n\n[mcp_servers.other]\ncommand = "keep"\n',
        encoding="utf-8",
    )

    with pytest.raises(IntegrationError):
        install("codex", root, use_python=True, home=home)
    install("codex", root, use_python=True, home=home, force=True)

    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["mcp_servers"]["verity"]["command"] != "old"
    assert parsed["mcp_servers"]["other"]["command"] == "keep"


def test_opencode_uses_local_command_array(root: Path, tmp_path: Path):
    install("opencode", root, use_python=True, home=tmp_path / "home")

    data = read_json(root / "opencode.json")
    server = data["mcp"]["servers"]["verity"]
    assert data["$schema"] == "https://opencode.ai/config.json"
    assert server["type"] == "local"
    assert "disabled" not in server
    assert server["command"][1:] == ["-m", "verity", "mcp"]


def test_invalid_json_requires_force(root: Path, tmp_path: Path):
    (root / ".mcp.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(IntegrationError):
        install("claude", root, home=tmp_path / "home")

    install("claude", root, home=tmp_path / "home", force=True)
    assert "verity" in read_json(root / ".mcp.json")["mcpServers"]


def test_claude_hook_is_installed_and_idempotent(root: Path, tmp_path: Path):
    install("claude", root, use_python=True, home=tmp_path / "home", with_hooks=True)
    install("claude", root, use_python=True, home=tmp_path / "home", with_hooks=True)

    settings = read_json(root / ".claude" / "settings.json")
    groups = settings["hooks"]["PreToolUse"]
    handlers = [handler for group in groups for handler in group["hooks"]]
    assert len(handlers) == 1
    assert handlers[0]["args"] == ["-m", "verity", "hook", "guard"]
    assert "Bash" in groups[0]["matcher"]


def test_claude_hook_preserves_existing_hooks(root: Path, tmp_path: Path):
    path = root / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "other"}]}
                    ],
                    "Stop": [{"hooks": [{"type": "command", "command": "notify"}]}],
                }
            }
        ),
        encoding="utf-8",
    )
    install("claude", root, use_python=True, home=tmp_path / "home", with_hooks=True)

    data = read_json(path)
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "notify"
    commands = [
        " ".join([handler["command"], *handler.get("args", [])])
        for group in data["hooks"]["PreToolUse"]
        for handler in group["hooks"]
    ]
    assert "other" in commands
    assert any("verity" in command for command in commands)


def test_generic_prints_without_writing(root: Path, tmp_path: Path):
    result = install("generic", root, use_python=True, home=tmp_path / "home")

    assert result.changes[0].status == "printed"
    snippet = json.loads(result.changes[0].detail)
    assert snippet["mcpServers"]["verity"]["args"] == ["-m", "verity", "mcp"]
    assert not (root / ".mcp.json").exists()


def test_server_spec_command_string(tmp_path: Path):
    executable, args = server_spec(tmp_path, command="uvx verity")
    assert executable == "uvx"
    assert args == ["verity", "mcp"]
