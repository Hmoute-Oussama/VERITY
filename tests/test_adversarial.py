from __future__ import annotations

from pathlib import Path

from verity.evidence.registry import verify_claim
from verity.guard import check_action
from verity.models import ClaimDraft


def _verify(engine, verifier: str, **args):
    claim = ClaimDraft(text="adversarial test claim", verifier=verifier, args=args).to_claim()
    return verify_claim(claim, engine.context())


def test_symbol_verifier_rejects_path_escape(engine):
    outside = engine.root.parent / "outside.py"
    outside.write_text("def escaped_symbol():\n    pass\n", encoding="utf-8")
    result = _verify(engine, "code_symbol", symbol="escaped_symbol", path="../outside.py")
    assert result.status.value == "fail"
    assert not outside.is_relative_to(engine.root)


def test_guard_blocks_path_escape_python_script(engine):
    decision = check_action(engine, "python ../outside.py")
    assert decision.verdict.value == "block"
    assert any("outside" in check.description.lower() for check in decision.checks)


def test_openapi_verifier_rejects_path_escape(engine):
    outside = engine.root.parent / "openapi.yaml"
    outside.write_text("openapi: 3.0.0\npaths:\n  /login:\n    post: {}\n", encoding="utf-8")
    result = _verify(engine, "openapi", path="../openapi.yaml", route="/login", method="POST")
    assert result.status.value == "fail"


def test_secret_scan_redacts_token(engine):
    secret = "ghp_" + ("A" * 36)
    (engine.root / "config.py").write_text(f"GITHUB_TOKEN = '{secret}'\n", encoding="utf-8")
    result = _verify(engine, "secret_scan", paths=["config.py"])
    assert secret not in result.details
    assert secret not in str(result.payload)


def test_malformed_yaml_is_not_executed(engine):
    (engine.root / "compose.yaml").write_text("services:\n  web: [unterminated\n", encoding="utf-8")
    result = _verify(engine, "config_valid", paths=["compose.yaml"])
    assert result.status.value == "fail"


def test_unknown_verifier_is_an_error_without_crash(engine):
    result = _verify(engine, "definitely_unknown_verifier")
    assert result.status.value == "error"
    assert "Unknown verifier" in result.summary
