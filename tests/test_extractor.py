from __future__ import annotations

from pathlib import Path

from verity.claims.extractor import (
    extract_from_changes,
    extract_from_commands,
    extract_from_text,
)
from verity.gitutils import Changes, FileChange


def texts(claims) -> list[str]:
    return [c.text for c in claims]


def by_verifier(claims, verifier: str):
    return [c for c in claims if c.verifier == verifier]


def test_headline_sentence_decomposes_into_independent_claims():
    claims = extract_from_text(
        "I implemented JWT authentication using PyJWT, added the login endpoint, "
        "and all tests pass."
    )
    assert any(c.verifier == "test_execution" for c in claims)
    assert any(c.verifier == "dependency" for c in claims)
    assert any(c.verifier == "code_route" for c in claims)
    # The feature statement is kept as an explicit assumption, not silently dropped.
    feature_claims = [c for c in claims if c.verifier == "none"]
    assert feature_claims
    assert any("authentication" in c.text.lower() for c in feature_claims)


def test_agent_success_is_never_self_verifying():
    claims = extract_from_text("Everything works and the task is complete.")
    assert claims
    assert all(c.verifier == "none" for c in claims)
    assert all(c.kind == "completion" for c in claims)


def test_schema_and_migration_claims():
    claims = extract_from_text(
        "I updated the database schema, migrated the users table, and fixed the API."
    )
    schema = [c for c in claims if c.verifier == "database_schema"]
    assert any("users" in c.text for c in schema)
    assert any("schema" in c.text.lower() for c in schema)


def test_test_claim_preserves_explicit_command():
    claims = extract_from_text("The mvn test suite passed.")
    tests = by_verifier(claims, "test_execution")
    assert tests and tests[0].args.get("command") == "mvn test"


def test_explicit_import_creates_dependency_claim():
    claims = extract_from_text("from payment_sdk import charge_customer")
    deps = by_verifier(claims, "dependency")
    assert deps and deps[0].args["name"] == "payment_sdk"


def test_known_stdlib_import_is_not_a_claim():
    claims = extract_from_text("import os\nfrom pathlib import Path")
    assert not by_verifier(claims, "dependency")


def test_file_and_endpoint_claims():
    claims = extract_from_text("I created docker-compose.yml and added the POST /api/login endpoint.")
    assert any(c.verifier == "file_exists" for c in claims)
    routes = by_verifier(claims, "code_route")
    assert routes and routes[0].args["path"] == "/api/login"
    assert routes[0].args["method"] == "POST"


def test_commands_create_precondition_claims():
    claims = extract_from_commands(
        [
            "docker compose up --build",
            "npm run build",
            "python scripts/migrate.py",
            "mvn test",
        ]
    )
    descriptions = " ".join(texts(claims)).lower()
    assert "docker compose" in descriptions
    assert "npm script" in descriptions
    assert "scripts/migrate.py" in descriptions
    assert "maven" in descriptions


def test_diff_extraction_finds_symbols_imports_and_routes(tmp_path: Path):
    changes = Changes(
        files=[
            FileChange(
                path="app.py",
                status="modified",
                added_lines=[
                    "from payment_sdk import charge_customer",
                    "",
                    "def refresh_token(token: str) -> dict:",
                    "    return {}",
                    "",
                    '@app.post("/refresh")',
                    "def refresh() -> dict:",
                    "    return {}",
                ],
            )
        ],
        is_repo=True,
    )
    claims = extract_from_changes(changes, tmp_path)
    verifiers = {c.verifier for c in claims}
    assert "code_symbol" in verifiers
    assert "code_route" in verifiers
    assert "imports" in verifiers
    assert "syntax" in verifiers
    assert any(c.verifier == "dependency" and c.args["name"] == "payment_sdk" for c in claims)


def test_extraction_deduplicates_identical_claims():
    text = "Tests pass. All tests pass. The test suite passed."
    claims = extract_from_text(text)
    tests = by_verifier(claims, "test_execution")
    assert len(tests) == 1


def test_unknown_feature_is_not_turned_into_noise():
    claims = extract_from_text("I implemented a completely unrelated thing.")
    assert not [c for c in claims if c.kind == "behavior"]
