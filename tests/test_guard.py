from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from verity.guard import check_action, scan_assumptions


def test_missing_compose_file_blocks(engine):
    decision = check_action(engine, "docker compose up --build")
    assert decision.verdict.value == "block"
    assert any("compose" in c.description.lower() for c in decision.missing)


def test_valid_compose_file_allows(engine):
    (engine.root / "compose.yaml").write_text("services:\n  web:\n    image: nginx\n")
    decision = check_action(engine, "docker compose up --build")
    assert decision.verdict.value == "allow"
    assert all(c.satisfied for c in decision.checks)


def test_compose_without_services_blocks(engine):
    (engine.root / "compose.yaml").write_text("version: '3'\n")
    decision = check_action(engine, "docker compose up")
    assert decision.verdict.value == "block"
    assert any("service" in c.description.lower() for c in decision.missing)


def test_missing_npm_script_blocks(engine):
    (engine.root / "package.json").write_text('{"scripts": {"test": "jest"}}')
    decision = check_action(engine, "npm run build")
    assert decision.verdict.value == "block"
    assert any("build" in c.description for c in decision.missing)


def test_existing_npm_script_allows(engine):
    (engine.root / "package.json").write_text('{"scripts": {"build": "tsc"}}')
    decision = check_action(engine, "npm run build")
    assert decision.verdict.value == "allow"


def test_python_script_with_missing_import_blocks(engine):
    (engine.root / "payment.py").write_text("from payment_sdk import charge_customer\n")
    decision = check_action(engine, "python payment.py")
    assert decision.verdict.value == "block"
    assert any("payment_sdk" in c.evidence for c in decision.missing)


def test_python_script_missing_file_blocks(engine):
    decision = check_action(engine, "python nope.py")
    assert decision.verdict.value == "block"
    assert any("nope.py" in c.description for c in decision.missing)


def test_python_script_with_local_imports_allows(engine):
    (engine.root / "helper.py").write_text("VALUE = 1\n")
    (engine.root / "script.py").write_text("import helper\nprint(helper.VALUE)\n")
    decision = check_action(engine, "python script.py")
    assert decision.verdict.value == "allow"


def test_unknown_action_is_not_blocked(engine):
    decision = check_action(engine, "echo hello")
    assert decision.verdict.value == "allow"


def test_strict_mode_blocks_medium_risk(engine):
    decision = check_action(engine, "pytest missing_tests", strict=True)
    assert decision.verdict.value == "block"


def test_deny_policy_blocks(engine):
    engine.config.policy.deny_commands = [r"git\s+push"]
    decision = check_action(engine, "git push origin main")
    assert decision.verdict.value == "block"
    assert "deny" in decision.reasons[0]


def test_allow_policy_overrides(engine):
    engine.config.policy.allow_commands = [r"docker\s+compose"]
    decision = check_action(engine, "docker compose up")
    assert decision.verdict.value == "allow"


def test_makefile_target_check(engine):
    decision = check_action(engine, "make deploy")
    assert decision.verdict.value == "block"
    (engine.root / "Makefile").write_text("deploy:\n\techo deploy\n")
    decision = check_action(engine, "make deploy")
    assert decision.verdict.value == "allow"


def test_guard_handles_chained_commands(engine):
    decision = check_action(engine, "pytest tests && docker compose up")
    assert decision.verdict.value == "block"


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=T", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(subprocess.run(["git", "--version"], capture_output=True).returncode != 0, reason="git required")
def test_scan_finds_missing_imports_in_working_tree(engine):
    _git(engine.root, "init", "-q")
    _git(engine.root, "add", "-A")
    _git(engine.root, "commit", "-qm", "init")
    (engine.root / "payment.py").write_text("from payment_sdk import charge_customer\n")
    findings = scan_assumptions(engine)
    assert any(f.kind == "missing_dependency" and "payment_sdk" in f.description for f in findings)


@pytest.mark.skipif(subprocess.run(["git", "--version"], capture_output=True).returncode != 0, reason="git required")
def test_scan_finds_invalid_config(engine):
    _git(engine.root, "init", "-q")
    _git(engine.root, "add", "-A")
    _git(engine.root, "commit", "-qm", "init")
    (engine.root / "broken.yaml").write_text("key: [unclosed\n")
    findings = scan_assumptions(engine)
    assert any(f.kind == "invalid_config" for f in findings)
