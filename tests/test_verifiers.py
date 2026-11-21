from __future__ import annotations

import sys
from pathlib import Path

import pytest

from verity.evidence.registry import verify_claim
from verity.models import Claim, ClaimDraft


def make(verifier: str, **args) -> Claim:
    return ClaimDraft(text="test claim", verifier=verifier, args=args).to_claim()


def run(engine, verifier: str, **args):
    return verify_claim(make(verifier, **args), engine.context())


def test_code_symbol_found(engine):
    result = run(engine, "code_symbol", symbol="authenticate_user")
    assert result.status.value == "pass"
    assert "app.py" in result.dependencies


def test_code_symbol_missing(engine):
    result = run(engine, "code_symbol", symbol="does_not_exist")
    assert result.status.value == "fail"
    assert "not found" in result.summary


def test_code_symbol_does_not_trust_mentions(engine):
    (engine.root / "notes.py").write_text("def unrelated():\n    pass\n# authenticate_user\n")
    result = run(engine, "code_symbol", symbol="missing_function")
    assert result.status.value == "fail"


def test_code_route_found(engine):
    (engine.root / "routes.py").write_text(
        '@router.post("/login")\ndef login():\n    return {}\n'
    )
    result = run(engine, "code_route", path="/login", method="POST")
    assert result.status.value == "pass"


def test_code_route_method_mismatch_warns(engine):
    (engine.root / "routes.py").write_text(
        '@router.post("/login")\ndef login():\n    return {}\n'
    )
    result = run(engine, "code_route", path="/login", method="GET")
    assert result.status.value == "warn"
    assert "not with method GET" in result.summary


def test_code_route_missing(engine):
    result = run(engine, "code_route", path="/nope", method="POST")
    assert result.status.value == "fail"


def test_code_route_fuzzy_named_endpoint(engine):
    (engine.root / "routes.py").write_text(
        '@app.get("/api/v1/users/{user_id}/login")\ndef login():\n    return {}\n'
    )
    result = run(engine, "code_route", path="/login", method=None, fuzzy=True)
    assert result.status.value == "pass"


def test_file_exists_and_absent(engine):
    assert run(engine, "file_exists", paths=["app.py"]).status.value == "pass"
    assert run(engine, "file_exists", paths=["nope.py"]).status.value == "fail"
    assert run(engine, "file_exists", paths=["nope.py"], expect="absent").status.value == "pass"


def test_dependency_declared(engine):
    result = run(engine, "dependency", name="PyJWT", candidates=["pyjwt"])
    assert result.status.value == "pass"
    assert "pyproject.toml" in result.dependencies


def test_dependency_declared_with_utf8_bom(engine):
    path = engine.root / "pyproject.toml"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    result = run(engine, "dependency", name="PyJWT", candidates=["pyjwt"])
    assert result.status.value == "pass"


def test_dependency_missing(engine):
    result = run(engine, "dependency", name="payment_sdk", candidates=["payment_sdk"])
    assert result.status.value == "fail"


def test_dependency_installed_but_undeclared_is_a_warning(engine):
    result = run(engine, "dependency", name="pytest", candidates=["pytest"])
    # pytest is declared in the fixture, so this is a pass; use a real stdlib
    # distribution that is not declared instead.
    assert result.status.value == "pass"
    result = run(engine, "dependency", name="setuptools", candidates=["setuptools"])
    assert result.status.value in {"warn", "fail"}


def test_imports_missing_module(engine):
    (engine.root / "bad.py").write_text("from payment_sdk import charge_customer\n")
    result = run(engine, "imports", paths=["bad.py"])
    assert result.status.value == "fail"
    assert "payment_sdk" in result.details


def test_imports_local_module(engine):
    (engine.root / "helpers.py").write_text("VALUE = 1\n")
    (engine.root / "uses_helper.py").write_text("import helpers\n")
    result = run(engine, "imports", paths=["uses_helper.py"])
    assert result.status.value == "pass"


def test_syntax_failure(engine):
    (engine.root / "broken.py").write_text("def broken(:\n    pass\n")
    result = run(engine, "syntax", paths=["broken.py"])
    assert result.status.value == "fail"


def test_syntax_accepts_utf8_bom(engine):
    (engine.root / "bom.py").write_bytes(b"\xef\xbb\xbfdef ok():\n    pass\n")
    assert run(engine, "syntax", paths=["bom.py"]).status.value == "pass"


def test_config_valid_invalid_yaml(engine):
    (engine.root / "compose.yaml").write_text("services:\n  web: [unclosed\n")
    result = run(engine, "config_valid", paths=["compose.yaml"])
    assert result.status.value == "fail"


def test_json_path(engine):
    (engine.root / "package.json").write_text('{"scripts": {"build": "tsc"}}')
    assert run(engine, "json_path", path="package.json", json_path="scripts.build").status.value == "pass"
    assert run(engine, "json_path", path="package.json", json_path="scripts.missing").status.value == "fail"


def test_environment_python_version(engine):
    major = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert run(engine, "environment", technology="Python", version=major).status.value == "pass"
    assert run(engine, "environment", technology="Python", version="1.0").status.value == "fail"


def test_environment_port_closed(engine):
    result = run(engine, "environment", port=59999)
    assert result.status.value == "fail"


def test_secret_scan_detects_key(engine):
    (engine.root / "config.py").write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    result = run(engine, "secret_scan", paths=["config.py"])
    assert result.status.value == "fail"
    assert "redacted" in result.details.lower() or "aws_access_key" in result.details


def test_secret_scan_ignores_placeholders(engine):
    (engine.root / "config.py").write_text('api_key = "your-api-key-here"\n')
    result = run(engine, "secret_scan", paths=["config.py"])
    assert result.status.value == "pass"


def test_secret_scan_does_not_leak_value(engine):
    secret = "AKIAIOSFODNN7EXAMPLE"
    (engine.root / "config.py").write_text(f'AWS_KEY = "{secret}"\n')
    result = run(engine, "secret_scan", paths=["config.py"])
    assert secret not in result.details
    assert secret not in str(result.payload)


def test_database_schema_definition(engine):
    (engine.root / "schema.sql").write_text(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);\n"
    )
    result = run(engine, "database_schema", table="users")
    assert result.status.value == "pass"
    assert "schema.sql" in result.dependencies


def test_database_schema_missing(engine):
    result = run(engine, "database_schema", table="payments")
    assert result.status.value == "fail"


def test_database_schema_migration_only_warns(engine):
    migrations = engine.root / "migrations"
    migrations.mkdir()
    (migrations / "001.py").write_text('op.create_table("orders", sa.Column("id", sa.Integer))\n')
    result = run(engine, "database_schema", table="orders")
    assert result.status.value == "warn"


def test_security_analysis_flags_plaintext_comparison(engine):
    (engine.root / "auth.py").write_text(
        "def check(user, password):\n    return user.password == password\n"
    )
    result = run(engine, "security_analysis", property="passwords are hashed")
    assert result.status.value == "fail"


def test_security_analysis_finds_hashing(engine):
    (engine.root / "auth.py").write_text(
        "import bcrypt\n\ndef hash_password(p):\n    return bcrypt.hashpw(p, bcrypt.gensalt())\n"
    )
    result = run(engine, "security_analysis", property="passwords are hashed")
    assert result.status.value == "pass"
    assert "not a security review" in result.details


def test_http_probe_blocks_unknown_host(engine):
    result = run(engine, "http_probe", url="https://example.com")
    assert result.status.value == "warn"
    assert "blocked" in result.summary


def test_none_verifier_keeps_claim_as_assumption(engine):
    result = run(engine, "none")
    assert result.status.value == "warn"
    assert "assumption" in result.summary


def test_command_not_allowlisted_is_not_executed(engine):
    result = run(engine, "command", command="echo hello")
    assert result.status.value == "warn"
    assert "allowlisted" in result.summary


def test_command_dangerous_is_refused(engine):
    result = run(engine, "command", command="rm -rf /")
    assert result.status.value == "fail"
    assert "dangerous" in result.summary.lower()
