"""Local HTTP API + dashboard for VERITY (stdlib only)."""

from __future__ import annotations

import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..contracts import check_contracts, load_contracts
from ..engine import Engine
from ..graph import build_graph
from ..guard import check_action
from ..snapshot import build_agent_context, build_snapshot
from ..util import truncate

DASHBOARD = Path(__file__).parent / "dashboard.html"


def _evidence_dict(engine: Engine, claim_id: str) -> list[dict[str, Any]]:
    out = []
    for evidence in engine.repo.evidence_for(claim_id):
        deps = engine.repo.evidence_deps(evidence.id)
        out.append(
            {
                "id": evidence.id,
                "verifier": evidence.verifier,
                "status": evidence.status,
                "summary": evidence.summary,
                "details": truncate(evidence.details, 4000),
                "valid": evidence.valid,
                "invalidated_reason": evidence.invalidated_reason,
                "confidence": evidence.confidence,
                "commit": evidence.commit_sha,
                "created_at": evidence.created_at,
                "checked_at": evidence.checked_at,
                "dependencies": list(deps.keys()),
            }
        )
    return out


def status_payload(engine: Engine) -> dict[str, Any]:
    status = engine.status()
    claims = []
    for claim in status.claims:
        claims.append(
            {
                "id": claim.id,
                "text": claim.text,
                "kind": claim.kind,
                "verifier": claim.verifier,
                "status": claim.status,
                "required": claim.required,
                "contract": claim.contract_id,
                "source": claim.source,
                "confidence": claim.confidence,
                "args": claim.args,
                "evidence": _evidence_dict(engine, claim.id),
            }
        )
    invalidated = [
        {
            "claim_id": e.claim_id,
            "summary": e.summary,
            "reason": e.invalidated_reason,
            "verifier": e.verifier,
        }
        for e in status.evidence
        if not e.valid
    ]
    contracts = load_contracts(engine.root)
    return {
        "project": status.project,
        "commit": status.commit,
        "branch": status.branch,
        "overall": status.overall,
        "counts": status.counts,
        "contracts": {
            "met": status.contracts_met,
            "total": status.contracts_total,
            "task": contracts.task,
            "defined": len(contracts.contracts),
        },
        "evidence": {
            "total": len(status.evidence),
            "valid": sum(1 for e in status.evidence if e.valid),
            "invalidated": len(invalidated),
            "failed": sum(1 for e in status.evidence if e.valid and e.status == "fail"),
        },
        "invalidations": invalidated,
        "claims": claims,
        "agent_context": build_agent_context(engine),
    }


class VerityHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], engine: Engine):
        super().__init__(address, Handler)
        self.engine = engine
        self.lock = threading.RLock()


class Handler(BaseHTTPRequestHandler):
    server_version = "VERITY/0.1"
    protocol_version = "HTTP/1.1"

    @property
    def engine(self) -> Engine:
        return self.server.engine  # type: ignore[attr-defined]

    @property
    def lock(self) -> threading.RLock:
        return self.server.lock  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep the terminal calm; errors still surface through responses.
        return

    # ------------------------------------------------------------------ helpers
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------ routes
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        try:
            if path in {"/", "/index.html"}:
                if DASHBOARD.is_file():
                    self._send(200, DASHBOARD.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send(200, b"<h1>VERITY</h1><p>Dashboard asset missing.</p>", "text/html")
                return
            if path == "/health":
                self._json({"ok": True, "project": self.engine.repo.project})
                return
            if path == "/api/status":
                with self.lock:
                    self._json(status_payload(self.engine))
                return
            if path == "/api/snapshot":
                compact = query.get("compact", ["0"])[0] in {"1", "true", "yes"}
                with self.lock:
                    status = self.engine.status()
                self._send(
                    200,
                    build_snapshot(status, compact=compact).encode("utf-8"),
                    "text/markdown; charset=utf-8",
                )
                return
            if path == "/api/graph":
                with self.lock:
                    self._json(build_graph(self.engine).to_dict())
                return
            if path == "/api/contracts":
                with self.lock:
                    contracts = load_contracts(self.engine.root)
                    claims = self.engine.repo.claims()
                    payload = {
                        "task": contracts.task,
                        "contracts": [
                            {
                                "id": c.id,
                                "claim": c.claim,
                                "verifier": c.verifier,
                                "required": c.required,
                                "args": c.args,
                                "status": next(
                                    (cl.status for cl in claims if cl.contract_id == c.id), "unverified"
                                ),
                                "evidence": next(
                                    (
                                        _evidence_dict(self.engine, cl.id)
                                        for cl in claims
                                        if cl.contract_id == c.id
                                    ),
                                    [],
                                ),
                            }
                            for c in contracts.contracts
                        ],
                    }
                self._json(payload)
                return
            if path.startswith("/api/claims/"):
                claim_id = path.rsplit("/", 1)[-1]
                with self.lock:
                    claim = self.engine.repo.get_claim(claim_id)
                    if not claim:
                        self._error(404, "claim not found")
                        return
                    self._json(
                        {
                            "id": claim.id,
                            "text": claim.text,
                            "status": claim.status,
                            "verifier": claim.verifier,
                            "args": claim.args,
                            "history": [
                                {
                                    "id": e.id,
                                    "status": e.status,
                                    "valid": e.valid,
                                    "superseded": e.superseded,
                                    "summary": e.summary,
                                    "reason": e.invalidated_reason,
                                    "created_at": e.created_at,
                                }
                                for e in self.engine.repo.evidence_history(claim.id)
                            ],
                        }
                    )
                return
            self._error(404, f"unknown path: {path}")
        except Exception as exc:
            traceback.print_exc()
            self._error(500, f"{type(exc).__name__}: {exc}")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        body = self._body()
        try:
            if path == "/api/verify":
                with self.lock:
                    stale_only = bool(body.get("stale_only", False))
                    verifications = self.engine.verify(only_stale=stale_only)
                    self._json(
                        {
                            "verified": len(verifications),
                            "passed": sum(1 for v in verifications if v.evidence.status == "pass"),
                            "failed": sum(1 for v in verifications if v.evidence.status == "fail"),
                            "status": status_payload(self.engine),
                        }
                    )
                return
            if path == "/api/analyze":
                text = str(body.get("text") or "")
                if not text.strip():
                    self._error(400, "text is required")
                    return
                with self.lock:
                    result = self.engine.analyze(
                        text=text,
                        use_diff=bool(body.get("use_diff", True)),
                        verify=bool(body.get("verify", True)),
                    )
                    self._json(
                        {
                            "claims": [
                                {
                                    "id": c.id,
                                    "text": c.text,
                                    "status": (self.engine.repo.get_claim(c.id) or c).status,
                                }
                                for c in result.claims
                            ],
                            "new": len(result.new_claims),
                            "status": status_payload(self.engine),
                        }
                    )
                return
            if path == "/api/guard":
                command = str(body.get("command") or "")
                with self.lock:
                    decision = check_action(self.engine, command, strict=bool(body.get("strict", False)))
                self._json(decision.to_dict())
                return
            if path == "/api/contracts/check":
                with self.lock:
                    contracts = load_contracts(self.engine.root)
                    results = check_contracts(self.engine, contracts)
                    self._json(
                        {
                            "task": contracts.task,
                            "results": [
                                {
                                    "id": r.contract.id,
                                    "claim": r.contract.claim,
                                    "status": r.status,
                                    "met": r.met,
                                    "evidence": r.evidence_summary,
                                }
                                for r in results
                            ],
                            "status": status_payload(self.engine),
                        }
                    )
                return
            self._error(404, f"unknown path: {path}")
        except Exception as exc:
            traceback.print_exc()
            self._error(500, f"{type(exc).__name__}: {exc}")


def serve(root: Path, host: str = "127.0.0.1", port: int = 8787) -> None:
    engine = Engine(Path(root).resolve())
    httpd = VerityHTTPServer((host, port), engine)
    print(f"VERITY dashboard: http://{host}:{port}")
    print(f"Project: {engine.repo.project} ({engine.root})")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping VERITY server.")
    finally:
        httpd.server_close()
