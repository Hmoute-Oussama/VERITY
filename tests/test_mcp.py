from __future__ import annotations

import json

from verity.server.mcp import TOOLS, McpServer


def test_tool_catalog_exposes_the_reality_surface():
    names = {tool["name"] for tool in TOOLS}

    assert names == {
        "verity_reality",
        "verity_check_action",
        "verity_verify_claim",
        "verity_list_claims",
        "verity_verify",
        "verity_scan",
        "verity_contract_check",
    }


def test_check_action_blocks_missing_script(project):
    server = McpServer(project)
    try:
        text, is_error = server.call_tool(
            "verity_check_action", {"command": "python missing_script.py"}
        )
    finally:
        server.engine.repo.close()

    assert is_error is True
    assert "BLOCK" in text
    assert "missing_script.py" in text


def test_verify_claim_never_treats_prose_as_evidence(project):
    server = McpServer(project)
    try:
        text, is_error = server.call_tool(
            "verity_verify_claim", {"text": "I implemented authentication and all tests pass."}
        )
    finally:
        server.engine.repo.close()

    assert is_error is False
    assert "Claims analyzed" in text
    assert "test_execution" in text or "assumption" in text.lower()


def test_reality_resource_is_json_claims(project):
    server = McpServer(project)
    try:
        server.call_tool(
            "verity_verify_claim", {"text": "authenticate_user exists and tests pass."}
        )
        content, mime, uri = server.read_resource("verity://claims")
    finally:
        server.engine.repo.close()

    assert uri == "verity://claims"
    assert mime == "application/json"
    data = json.loads(content)
    assert isinstance(data["claims"], list)
    assert data["claims"]


def test_unknown_tool_returns_an_error(project):
    server = McpServer(project)
    try:
        text, is_error = server.call_tool("verity_nope", {})
    finally:
        server.engine.repo.close()

    assert is_error is True
    assert "Unknown tool" in text
