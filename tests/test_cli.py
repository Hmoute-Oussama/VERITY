from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from verity.cli import app

runner = CliRunner()


def test_verifier_catalog_is_machine_readable():
    result = runner.invoke(app, ["verifiers", "--json"])

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert "openapi" in output
    assert "migration_consistency" in output
    assert "lockfile_consistency" in output
    assert "sbom" in output


def test_version_json_exposes_stable_api_versions():
    result = runner.invoke(app, ["--version", "--json"])

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["name"] == "verity"
    assert output["api_version"] == "1.0"
    assert output["contract_schema_version"] == 1
    assert output["db_schema_version"] == 1


def test_validate_accepts_initialized_project(project: Path):
    from verity.config import scaffold_config
    from verity.contracts import scaffold_contracts

    scaffold_config(project)
    scaffold_contracts(project)
    result = runner.invoke(app, ["-C", str(project), "validate", "--json"])

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["valid"] is True


def test_validate_reports_contract_problem(tmp_path: Path):
    result = runner.invoke(app, ["-C", str(tmp_path), "validate", "--json"])

    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["valid"] is False
    assert output["issues"]


def test_doctor_fix_initializes_project(tmp_path: Path):
    result = runner.invoke(app, ["-C", str(tmp_path), "doctor", "--fix", "--json"])

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["initialized"] is True
    assert (tmp_path / ".verity" / "config.yaml").is_file()
    assert (tmp_path / ".verity" / "contracts.yaml").is_file()


def test_integrate_lists_supported_agents():
    result = runner.invoke(app, ["integrate", "--list"])

    assert result.exit_code == 0
    for name in ("claude", "codex", "opencode", "cursor", "vscode"):
        assert name in result.stdout


def test_integrate_writes_agent_config(project: Path):
    result = runner.invoke(
        app,
        ["-C", str(project), "integrate", "claude", "--python", "--no-instructions"],
    )

    assert result.exit_code == 0
    data = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert "verity" in data["mcpServers"]


def test_hook_guard_denies_missing_script(project: Path):
    payload = {"tool_name": "Bash", "tool_input": {"command": "python missing_script.py"}}
    result = runner.invoke(
        app,
        ["-C", str(project), "hook", "guard"],
        input=json.dumps(payload),
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "missing_script.py" in output["hookSpecificOutput"]["permissionDecisionReason"]


def test_hook_guard_warns_but_does_not_block(project: Path):
    payload = {"tool_name": "Bash", "tool_input": {"command": "pytest missing_tests"}}
    result = runner.invoke(
        app,
        ["-C", str(project), "hook", "guard"],
        input=json.dumps(payload),
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert "systemMessage" in output


def test_hook_guard_allows_safe_command(project: Path):
    payload = {"tool_name": "Bash", "tool_input": {"command": "echo hello"}}
    result = runner.invoke(
        app,
        ["-C", str(project), "hook", "guard"],
        input=json.dumps(payload),
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_hook_guard_json_mode_exits_nonzero_on_block(project: Path):
    result = runner.invoke(
        app,
        [
            "-C",
            str(project),
            "hook",
            "guard",
            "--command",
            "python missing_script.py",
            "--json",
        ],
    )

    assert result.exit_code == 2
    output = json.loads(result.stdout)
    assert output["verdict"] == "block"


def test_hook_guard_ignores_uninitialized_project(tmp_path: Path):
    payload = {"tool_name": "Bash", "tool_input": {"command": "python missing_script.py"}}
    result = runner.invoke(
        app,
        ["-C", str(tmp_path), "hook", "guard"],
        input=json.dumps(payload),
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_status_json_is_versioned(tmp_path: Path):
    result = runner.invoke(app, ["-C", str(tmp_path), "init", "--no-gitignore"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["-C", str(tmp_path), "status", "--json"])
    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["schema"] == "verity.output.v1"
    assert output["command"] == "status"
    assert "api_version" in output


def test_init_supports_project_templates(tmp_path: Path):
    result = runner.invoke(
        app,
        ["-C", str(tmp_path), "init", "--template", "docker", "--no-gitignore"],
    )
    assert result.exit_code == 0
    text = (tmp_path / ".verity" / "contracts.yaml").read_text(encoding="utf-8")
    assert "compose_config" in text


def test_export_evidence_bundle(project: Path):
    result = runner.invoke(
        app,
        ["-C", str(project), "export", "--out", str(project / "bundle.json")],
    )
    assert result.exit_code == 0
    data = json.loads((project / "bundle.json").read_text(encoding="utf-8"))
    assert data["schema"] == "verity.evidence-bundle.v1"
    assert data["integrity"]["algorithm"] == "sha256"
    assert len(data["integrity"]["digest"]) == 64


def test_plugins_catalog_is_safe_and_declarative(tmp_path: Path):
    (tmp_path / ".verity").mkdir()
    (tmp_path / ".verity" / "plugins.yaml").write_text(
        "plugins:\n"
        "  community:\n"
        "    name: Community verifier\n"
        "    package: verity-community\n"
        "    verifier: community_check\n"
        "    verified: false\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["-C", str(tmp_path), "plugins", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data[0]["id"] == "community"
    assert data[0]["verified"] is False


def test_export_supports_sarif(project: Path):
    path = project / "verity.sarif.json"
    result = runner.invoke(
        app,
        ["-C", str(project), "export", "--format", "sarif", "--out", str(path)],
    )
    assert result.exit_code == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == "2.1.0"
    assert data["runs"]


def test_export_supports_junit(project: Path):
    path = project / "verity.xml"
    result = runner.invoke(
        app,
        ["-C", str(project), "export", "--format", "junit", "--out", str(path)],
    )
    assert result.exit_code == 0
    assert "<testsuite" in path.read_text(encoding="utf-8")


def test_backup_restore_and_diagnostics(project: Path):
    backup = project / "backup.db"
    result = runner.invoke(app, ["-C", str(project), "backup", "--out", str(backup)])
    assert result.exit_code == 0
    assert backup.is_file()

    restored = project / "restored"
    restored.mkdir()
    (restored / ".verity").mkdir()
    result = runner.invoke(
        app,
        ["-C", str(restored), "restore", str(backup)],
    )
    assert result.exit_code == 0

    result = runner.invoke(app, ["-C", str(project), "diagnostics", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["initialized"] is True
    assert data["database"]["integrity"] == "ok"


def test_audit_export(project: Path):
    out = project / "audit.json"
    result = runner.invoke(app, ["-C", str(project), "audit", "--out", str(out)])
    assert result.exit_code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == "verity.audit.v1"


def test_trust_list_and_add(tmp_path: Path):
    public_key = tmp_path / "release-public.pem"
    public_key.write_text(
        "-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["-C", str(tmp_path), "trust", "add", "--name", "release", "--public-key", str(public_key)],
    )
    assert result.exit_code == 0

    result = runner.invoke(app, ["-C", str(tmp_path), "trust", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["keys"][0]["name"] == "release"
