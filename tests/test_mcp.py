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


def test_initialize_reports_stable_api_version(project):
    server = McpServer(project)
    try:
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    finally:
        server.engine.repo.close()

    assert response["result"]["serverInfo"]["name"] == "verity"
    assert response["result"]["serverInfo"]["api_version"] == "1.0"


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


def test_mcp_ping_and_resource_catalog(project):
    server = McpServer(project)
    try:
        ping = server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        tools = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        resources = server.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
    finally:
        server.engine.repo.close()

    assert ping["result"] == {}
    assert {tool["name"] for tool in tools["result"]["tools"]} == {
        "verity_reality",
        "verity_check_action",
        "verity_verify_claim",
        "verity_list_claims",
        "verity_verify",
        "verity_scan",
        "verity_contract_check",
    }
    assert {resource["uri"] for resource in resources["result"]["resources"]} == {
        "verity://reality",
        "verity://claims",
        "verity://graph",
    }


def test_mcp_rejects_non_object_tool_arguments(project):
    server = McpServer(project)
    try:
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "verity_check_action", "arguments": "not-an-object"},
        })
    finally:
        server.engine.repo.close()

    assert response["error"]["code"] == -32602
    assert "arguments must be an object" in response["error"]["message"]


def test_mcp_unknown_resource_returns_protocol_error(project):
    server = McpServer(project)
    try:
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "resources/read",
            "params": {"uri": "verity://unknown"},
        })
    finally:
        server.engine.repo.close()

    assert response["error"]["code"] == -32602


def test_mcp_unknown_method_returns_protocol_error(project):
    server = McpServer(project)
    try:
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "unknown/method",
            "params": {},
        })
    finally:
        server.engine.repo.close()

    assert response["error"]["code"] == -32601
