"""Canonical evidence-bundle export with optional Ed25519 signatures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .engine import Engine
from .models import Evidence


def canonical_json(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def export_evidence_bundle(engine: Engine) -> dict[str, Any]:
    claims = []
    evidence_items = []
    dependencies = []
    for claim in engine.repo.claims():
        claims.append(
            {
                "id": claim.id,
                "text": claim.text,
                "kind": claim.kind,
                "verifier": claim.verifier,
                "args": claim.args,
                "status": claim.status,
                "required": claim.required,
                "source": claim.source,
                "confidence": claim.confidence,
                "contract_id": claim.contract_id,
                "created_at": claim.created_at,
                "updated_at": claim.updated_at,
            }
        )
        for evidence in engine.repo.evidence_for(claim.id):
            evidence_items.append(
                {
                    "id": evidence.id,
                    "claim_id": evidence.claim_id,
                    "verifier": evidence.verifier,
                    "status": evidence.status,
                    "summary": evidence.summary,
                    "details": evidence.details,
                    "payload": evidence.payload,
                    "confidence": evidence.confidence,
                    "commit_sha": evidence.commit_sha,
                    "valid": evidence.valid,
                    "superseded": evidence.superseded,
                    "invalidated_reason": evidence.invalidated_reason,
                    "created_at": evidence.created_at,
                    "checked_at": evidence.checked_at,
                    "dependencies": engine.repo.evidence_deps(evidence.id),
                }
            )
            for path, digest in engine.repo.evidence_deps(evidence.id).items():
                dependencies.append({"evidence_id": evidence.id, "path": path, "digest": digest})

    status = engine.status()
    body = {
        "schema": "verity.evidence-bundle.v1",
        "project": status.project,
        "commit": status.commit,
        "branch": status.branch,
        "overall": status.overall,
        "claims": claims,
        "evidence": evidence_items,
        "dependencies": dependencies,
    }
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    body["integrity"] = {"algorithm": "sha256", "digest": digest}
    return body


def sign_bundle(bundle: dict[str, Any], private_key_pem: str) -> dict[str, Any]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise RuntimeError("Ed25519 signing requires the 'cryptography' package") from exc
    key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise RuntimeError("Evidence bundle signing requires an Ed25519 private key")
    unsigned = dict(bundle)
    unsigned.pop("signature", None)
    signature = key.sign(canonical_json(unsigned)).hex()
    bundle["signature"] = {"algorithm": "ed25519", "value": signature}
    return bundle


def write_evidence_bundle(engine: Engine, path: Path, *, private_key: Path | None = None) -> Path:
    bundle = export_evidence_bundle(engine)
    if private_key is not None:
        sign_bundle(bundle, private_key.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path
