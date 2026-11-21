"""Evidence Contracts: what must be proven before a task may be called done."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONTRACTS, contracts_path
from .engine import Engine
from .models import Claim, ClaimDraft, ClaimKind, ClaimStatus
from .util import load_yaml

VERIFIER_KINDS = {
    "code_symbol": ClaimKind.EXISTENCE.value,
    "code_route": ClaimKind.EXISTENCE.value,
    "openapi": ClaimKind.CONFIG.value,
    "migration_consistency": ClaimKind.SCHEMA.value,
    "lockfile_consistency": ClaimKind.DEPENDENCY.value,
    "sbom": ClaimKind.SECURITY.value,
    "file_exists": ClaimKind.EXISTENCE.value,
    "syntax": ClaimKind.CONFIG.value,
    "imports": ClaimKind.DEPENDENCY.value,
    "config_valid": ClaimKind.CONFIG.value,
    "json_path": ClaimKind.CONFIG.value,
    "dependency": ClaimKind.DEPENDENCY.value,
    "test_execution": ClaimKind.TEST.value,
    "environment": ClaimKind.ENVIRONMENT.value,
    "secret_scan": ClaimKind.SECURITY.value,
    "security_analysis": ClaimKind.SECURITY.value,
    "database_schema": ClaimKind.SCHEMA.value,
    "http_probe": ClaimKind.BEHAVIOR.value,
    "deployment_config": ClaimKind.DEPLOYMENT.value,
    "command": ClaimKind.BEHAVIOR.value,
    "manual": ClaimKind.GENERIC.value,
    "none": ClaimKind.GENERIC.value,
}


@dataclass
class Contract:
    id: str
    claim: str
    verifier: str
    args: dict[str, Any] = field(default_factory=dict)
    required: bool = True
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int) -> "Contract":
        cid = str(data.get("id") or f"contract_{index + 1}").strip()
        claim_text = str(data.get("claim") or data.get("description") or "").strip()
        verifier = str(data.get("verifier") or "manual").strip()
        args = data.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        if data.get("command"):
            args = {**args, "command": str(data["command"])}
        if data.get("path") and "path" not in args and "paths" not in args:
            args["path"] = str(data["path"])
        return cls(
            id=cid,
            claim=claim_text or cid,
            verifier=verifier,
            args=args,
            required=bool(data.get("required", True)),
            description=str(data.get("description") or ""),
        )

    def to_draft(self, task: str | None = None) -> ClaimDraft:
        return ClaimDraft(
            text=self.claim,
            kind=VERIFIER_KINDS.get(self.verifier, ClaimKind.GENERIC.value),
            verifier=self.verifier,
            args=self.args,
            required=self.required,
            source="contract",
            confidence=1.0,
            contract_id=self.id,
            meta={"task": task} if task else {},
        )


@dataclass
class ContractsFile:
    version: int = 1
    task: str = ""
    contracts: list[Contract] = field(default_factory=list)
    path: Path | None = None

    def by_id(self) -> dict[str, Contract]:
        return {c.id: c for c in self.contracts}


@dataclass
class ContractResult:
    contract: Contract
    claim: Claim | None
    status: str
    evidence_summary: str = ""

    @property
    def met(self) -> bool:
        return self.status == ClaimStatus.VERIFIED.value


def load_contracts(root: Path) -> ContractsFile:
    path = contracts_path(root)
    if not path.is_file():
        return ContractsFile(path=path)
    data = load_yaml(path)
    if not isinstance(data, dict):
        return ContractsFile(path=path)
    raw = data.get("contracts") or []
    contracts: list[Contract] = []
    if isinstance(raw, dict):
        # Allow mapping form: {id: {claim, verifier, ...}}
        for i, (cid, spec) in enumerate(raw.items()):
            if isinstance(spec, dict):
                contracts.append(Contract.from_dict({"id": cid, **spec}, i))
    elif isinstance(raw, list):
        for i, item in enumerate(raw):
            if isinstance(item, dict):
                contracts.append(Contract.from_dict(item, i))
    return ContractsFile(
        version=int(data.get("version", 1) or 1),
        task=str(data.get("task") or ""),
        contracts=contracts,
        path=path,
    )


def scaffold_contracts(root: Path, *, force: bool = False) -> Path:
    path = contracts_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return path
    path.write_text(DEFAULT_CONTRACTS, encoding="utf-8")
    return path


def sync_contracts(engine: Engine, contracts: ContractsFile) -> list[Claim]:
    """Materialise contracts as required claims, removing stale ones."""
    claims: list[Claim] = []
    keep_keys: set[str] = set()
    for contract in contracts.contracts:
        draft = contract.to_draft(contracts.task)
        claim = engine.repo.upsert_claim(draft)
        claims.append(claim)
        keep_keys.add(claim.dedupe_key)
    # Remove contract claims that no longer exist or whose text changed.
    for claim in engine.repo.claims():
        if claim.source == "contract" and claim.dedupe_key not in keep_keys:
            engine.repo.delete_claim(claim.id)
    return claims


def check_contracts(engine: Engine, contracts: ContractsFile | None = None) -> list[ContractResult]:
    contracts = contracts or load_contracts(engine.root)
    claims = sync_contracts(engine, contracts)
    engine.verify_claims(claims)
    results: list[ContractResult] = []
    for contract, claim in zip(contracts.contracts, claims):
        refreshed = engine.repo.get_claim(claim.id) or claim
        evidence = engine.repo.evidence_for(refreshed.id)
        summary = ""
        if evidence:
            summary = evidence[0].summary
        results.append(
            ContractResult(
                contract=contract,
                claim=refreshed,
                status=refreshed.status,
                evidence_summary=summary,
            )
        )
    return results


def contract_summary(engine: Engine, contracts: ContractsFile | None = None) -> tuple[int, int]:
    contracts = contracts or load_contracts(engine.root)
    ids = {c.id for c in contracts.contracts}
    claims = [c for c in engine.repo.claims() if c.contract_id in ids]
    met = sum(1 for c in claims if c.status == ClaimStatus.VERIFIED.value)
    return met, len(contracts.contracts)
