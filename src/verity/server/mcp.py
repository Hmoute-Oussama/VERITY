"""VERITY MCP server (stdio, JSON-RPC 2.0, no external MCP SDK required).

Exposes the reality layer to any MCP-capable coding agent:

* ``verity_reality``      — current verified/unverified project state
* ``verity_check_action`` — the hallucination firewall
* ``verity_verify_claim`` — decompose + verify an agent statement
* ``verity_list_claims``  — inspect the evidence graph
* ``verity_verify``       — re-run verification
* ``verity_scan``         — find unsupported assumptions in the working tree
* ``verity_contract_check`` — enforce evidence contracts

Resources expose the snapshot, claims and graph as read-only documents.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

from .. import __version__
from ..contracts import check_contracts, load_contracts
from ..engine import Engine
from ..guard import check_action, scan_assumptions
from ..graph import build_graph
from ..snapshot import build_agent_context, build_snapshot
from ..util import truncate

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "verity"
SERVER_VERSION = __version__
API_VERSION = "1.0"
MAX_ARGUMENT_CHARS = 65536
MAX_JSON_CHARS = 262144


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "verity_reality",
        "Return VERITY's current reality snapshot for this repository: verified facts, "
        "unverified assumptions, contradicted claims and invalidated evidence. Call this "
        "before planning or claiming work is complete.",
        {
            "format": {
                "type": "string",
                "enum": ["markdown", "json"],
                "description": "markdown (compact, token-efficient) or json",
            }
        },
    ),
    _tool(
        "verity_check_action",
        "Check whether a shell action relies on assumptions that are demonstrably false "
        "(missing compose file, missing npm script, unresolved imports, missing migration). "
        "Returns allow/warn/block with exact evidence. Call before executing risky commands.",
        {
            "command": {"type": "string", "description": "Shell command to check"},
            "strict": {"type": "boolean", "description": "Block on any unmet prerequisite"},
        },
        ["command"],
    ),
    _tool(
        "verity_verify_claim",
        "Decompose an agent statement into claims and verify each against project evidence. "
        "Agent statements are never treated as evidence by themselves.",
        {
            "text": {"type": "string", "description": "The agent's natural-language claim(s)"},
            "use_diff": {
                "type": "boolean",
                "description": "Also derive claims from the current git diff (default true)",
            },
        },
        ["text"],
    ),
    _tool(
        "verity_list_claims",
        "List claims with their status and latest evidence. Useful for finding unverified "
        "assumptions before declaring a task complete.",
        {
            "status": {
                "type": "string",
                "enum": ["verified", "partially_verified", "unverified", "contradicted", "stale"],
            }
        },
    ),
    _tool(
        "verity_verify",
        "Re-run verifiers for claims. By default re-verifies everything; set stale_only to "
        "only refresh stale or unverified claims.",
        {"stale_only": {"type": "boolean"}},
    ),
    _tool(
        "verity_scan",
        "Scan the current working tree for unsupported assumptions: unresolved imports and "
        "invalid configuration. Returns findings with severity and file locations.",
        {},
    ),
    _tool(
        "verity_contract_check",
        "Evaluate the project's evidence contracts and report which required contracts are "
        "not yet satisfied. Use this before reporting a task as complete.",
        {},
    ),
]

RESOURCES = [
    {
        "uri": "verity://reality",
        "name": "Reality Snapshot",
        "description": "Compact, current verified/unverified project state.",
        "mimeType": "text/markdown",
    },
    {
        "uri": "verity://claims",
        "name": "Claims",
        "description": "All claims and statuses as JSON.",
        "mimeType": "application/json",
    },
    {
        "uri": "verity://graph",
        "name": "Evidence Graph",
        "description": "Claim → evidence → artifact graph as JSON.",
        "mimeType": "application/json",
    },
]


class McpServer:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.engine = Engine(self.root)

    # ------------------------------------------------------------------ tools
    def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        try:
            if name == "verity_reality":
                status = self.engine.status()
                if arguments.get("format") == "json":
                    return json.dumps(build_agent_context(self.engine), indent=2), False
                return build_snapshot(status, compact=True), False

            if name == "verity_check_action":
                decision = check_action(
                    self.engine,
                    str(arguments.get("command") or ""),
                    strict=bool(arguments.get("strict", False)),
                )
                text = (
                    f"VERDICT: {decision.verdict.value.upper()} (risk: {decision.risk.value})\n"
                    f"ACTION: {decision.action}\n"
                )
                if decision.reasons:
                    text += "\n".join(f"- {r}" for r in decision.reasons)
                return text, decision.verdict.value == "block"

            if name == "verity_verify_claim":
                text = str(arguments.get("text") or "")
                if len(text) > MAX_ARGUMENT_CHARS:
                    return "Claim text exceeds the 65536-character limit.", True
                use_diff = arguments.get("use_diff", True)
                if not text.strip():
                    return "No claim text was provided.", True
                result = self.engine.analyze(text=text, use_diff=bool(use_diff), verify=True)
                lines = [
                    f"Claims analyzed: {len(result.claims)}",
                    f"New claims: {len(result.new_claims)}",
                    "",
                ]
                for claim in result.claims:
                    refreshed = self.engine.repo.get_claim(claim.id) or claim
                    lines.append(f"- [{refreshed.status.upper()}] {refreshed.text}")
                    evidence = self.engine.repo.evidence_for(refreshed.id)
                    if evidence:
                        lines.append(f"  evidence: {evidence[0].summary}")
                if not result.claims:
                    lines.append("No verifiable claims were detected.")
                return "\n".join(lines), False

            if name == "verity_list_claims":
                status_filter = arguments.get("status")
                claims = self.engine.repo.claims(status=status_filter)
                if not claims:
                    return "No claims recorded.", False
                lines = []
                for claim in claims:
                    evidence = self.engine.repo.evidence_for(claim.id)
                    summary = f" — {evidence[0].summary}" if evidence else ""
                    lines.append(
                        f"[{claim.status.upper()}] {claim.text} ({claim.verifier}){summary}"
                    )
                return "\n".join(lines), False

            if name == "verity_verify":
                verifications = self.engine.verify(
                    only_stale=bool(arguments.get("stale_only", False))
                )
                status = self.engine.status()
                passed = sum(1 for v in verifications if v.evidence.status == "pass")
                failed = sum(1 for v in verifications if v.evidence.status in ("fail", "error"))
                return (
                    f"Verified {len(verifications)} claim(s): {passed} pass, {failed} fail.\n"
                    f"Overall: {status.overall}\n"
                    f"Counts: {json.dumps(status.counts)}"
                ), False

            if name == "verity_scan":
                findings = scan_assumptions(self.engine)
                if not findings:
                    return "No unsupported assumptions detected in the working tree.", False
                lines = [f"{len(findings)} assumption(s) need attention:", ""]
                for finding in findings:
                    lines.append(
                        f"- [{finding.severity.value.upper()}] {finding.description}\n"
                        f"  evidence: {finding.evidence}"
                        + (f"\n  hint: {finding.hint}" if finding.hint else "")
                    )
                return "\n".join(lines), any(f.severity.value in {"high", "critical"} for f in findings)

            if name == "verity_contract_check":
                contracts = load_contracts(self.root)
                if not contracts.contracts:
                    return "No evidence contracts are defined (.verity/contracts.yaml).", False
                results = check_contracts(self.engine, contracts)
                lines = [f"Task: {contracts.task or '(unnamed)'}", ""]
                unmet = 0
                for result in results:
                    mark = "PASS" if result.met else ("FAIL" if result.status == "contradicted" else "OPEN")
                    if not result.met:
                        unmet += 1
                    lines.append(
                        f"[{mark}] {result.contract.id}: {result.contract.claim}"
                        + (f"\n       {result.evidence_summary}" if result.evidence_summary else "")
                    )
                lines.append("")
                lines.append(f"{len(results) - unmet}/{len(results)} required contracts satisfied.")
                return "\n".join(lines), unmet > 0

            return f"Unknown tool: {name}", True
        except Exception as exc:  # never kill the agent's session
            return f"VERITY internal error: {type(exc).__name__}: {exc}", True

    # ------------------------------------------------------------------ resources
    def read_resource(self, uri: str) -> tuple[str, str, str]:
        if uri == "verity://reality":
            status = self.engine.status()
            return build_snapshot(status, compact=True), "text/markdown", uri
        if uri == "verity://claims":
            claims = []
            for claim in self.engine.repo.claims():
                evidence = self.engine.repo.evidence_for(claim.id)
                claims.append(
                    {
                        "id": claim.id,
                        "text": claim.text,
                        "status": claim.status,
                        "verifier": claim.verifier,
                        "required": claim.required,
                        "contract": claim.contract_id,
                        "evidence": [
                            {
                                "status": e.status,
                                "valid": e.valid,
                                "summary": e.summary,
                                "invalidated_reason": e.invalidated_reason,
                            }
                            for e in evidence
                        ],
                    }
                )
            return json.dumps({"claims": claims}, indent=2), "application/json", uri
        if uri == "verity://graph":
            return json.dumps(build_graph(self.engine).to_dict(), indent=2), "application/json", uri
        raise KeyError(uri)

    # ------------------------------------------------------------------ dispatch
    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
        if message.get("jsonrpc") != "2.0":
            return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32600, "message": "Invalid Request: jsonrpc must be 2.0"}}
        try:
            if len(json.dumps(message, ensure_ascii=False)) > MAX_JSON_CHARS:
                return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32600, "message": "Invalid Request: payload too large"}}
        except (TypeError, ValueError):
            return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32600, "message": "Invalid Request"}}
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}

        if method is None:
            return None
        if method.startswith("notifications/"):
            return None

        def ok(result: Any) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}

        def err(code: int, text: str) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

        if method == "initialize":
            return ok(
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}, "resources": {}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                        "api_version": API_VERSION,
                    },
                    "instructions": (
                        "VERITY is the reality layer for coding agents. "
                        "Call verity_reality before planning, verity_check_action before risky "
                        "shell commands, and verity_contract_check before declaring a task done. "
                        "No claim without evidence. No action without reality."
                    ),
                }
            )
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": TOOLS})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return err(-32602, "arguments must be an object")
            try:
                argument_size = len(json.dumps(arguments, ensure_ascii=False))
            except (TypeError, ValueError):
                return err(-32602, "arguments must be JSON-serializable")
            if argument_size > MAX_ARGUMENT_CHARS:
                return err(-32602, "arguments exceed the 65536-character limit")
            text, is_error = self.call_tool(str(name), arguments)
            return ok(
                {
                    "content": [{"type": "text", "text": truncate(text, 20000)}],
                    "isError": is_error,
                }
            )
        if method == "resources/list":
            return ok({"resources": RESOURCES})
        if method == "resources/read":
            uri = params.get("uri")
            try:
                text, mime, resolved = self.read_resource(str(uri))
            except KeyError:
                return err(-32602, f"Unknown resource: {uri}")
            return ok({"contents": [{"uri": resolved, "mimeType": mime, "text": truncate(text, 40000)}]})
        if method == "prompts/list":
            return ok({"prompts": []})
        return err(-32601, f"Method not found: {method}")


def _read_message(stream: Any) -> dict[str, Any] | None:
    line = stream.readline()
    if not line:
        return None
    stripped = line.strip()
    if not stripped:
        return {}
    if stripped.lower().startswith(b"content-length:"):
        try:
            length = int(stripped.split(b":", 1)[1].strip())
        except ValueError:
            return {}
        if length > MAX_JSON_CHARS:
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request: payload too large"}}
        while True:
            header = stream.readline()
            if header in (b"\r\n", b"\n", b""):
                break
        body = stream.read(length)
        return json.loads(body.decode("utf-8"))
    return json.loads(stripped.decode("utf-8"))


def run_stdio_server(root: Path) -> int:
    server = McpServer(root)
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    sys.stderr.write(
        f"VERITY MCP server v{SERVER_VERSION} — project: {server.engine.repo.project} "
        f"({server.root})\n"
    )
    sys.stderr.flush()

    while True:
        try:
            message = _read_message(stdin)
        except json.JSONDecodeError as exc:
            _write(stdout, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}})
            continue
        except Exception as exc:
            sys.stderr.write(f"VERITY: read error: {exc}\n")
            sys.stderr.flush()
            break
        if message is None:
            break
        if not message:
            continue
        try:
            response = server.handle(message)
        except Exception as exc:
            sys.stderr.write(f"VERITY: handler error: {exc}\n{traceback.format_exc()}\n")
            sys.stderr.flush()
            response = {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32603, "message": f"Internal error: {exc}"},
            }
        if response is not None:
            _write(stdout, response)
    return 0


def _write(stdout: Any, payload: dict[str, Any]) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
    stdout.flush()
