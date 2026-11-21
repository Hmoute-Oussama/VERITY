"""Core domain model for VERITY.

A *claim* is a statement an agent (or a human) makes about the software.
*Evidence* is the output of an independent check of that claim.
Evidence is tied to *artifacts* (files) so it can be invalidated when reality
changes underneath it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def stable_key(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.hexdigest()[:32]


class ClaimStatus(str, Enum):
    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    UNVERIFIED = "unverified"
    CONTRADICTED = "contradicted"
    STALE = "stale"

    @property
    def label(self) -> str:
        return {
            "verified": "verified",
            "partially_verified": "partially verified",
            "unverified": "unverified",
            "contradicted": "contradicted",
            "stale": "stale",
        }[self.value]


class EvidenceStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    ERROR = "error"
    STALE = "stale"


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}[self.value]


class Verdict(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


class ClaimKind(str, Enum):
    """What sort of reality a claim asserts."""

    EXISTENCE = "existence"
    BEHAVIOR = "behavior"
    TEST = "test"
    DEPENDENCY = "dependency"
    CONFIG = "config"
    ENVIRONMENT = "environment"
    SECURITY = "security"
    SCHEMA = "schema"
    DEPLOYMENT = "deployment"
    COMPLETION = "completion"
    GENERIC = "generic"


@dataclass
class Claim:
    id: str
    text: str
    kind: str
    verifier: str
    args: dict[str, Any] = field(default_factory=dict)
    status: str = ClaimStatus.UNVERIFIED.value
    required: bool = False
    source: str = "agent"
    confidence: float = 0.5
    contract_id: str | None = None
    dedupe_key: str = ""
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.dedupe_key:
            self.dedupe_key = stable_key(
                self.verifier, json.dumps(self.args, sort_keys=True), self.text.lower().strip()
            )

    @property
    def status_enum(self) -> ClaimStatus:
        return ClaimStatus(self.status)

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "kind": self.kind,
            "verifier": self.verifier,
            "args": json.dumps(self.args, sort_keys=True),
            "status": self.status,
            "required": int(self.required),
            "source": self.source,
            "confidence": self.confidence,
            "contract_id": self.contract_id,
            "dedupe_key": self.dedupe_key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "meta": json.dumps(self.meta, sort_keys=True),
        }

    @classmethod
    def from_row(cls, row: Any) -> "Claim":
        return cls(
            id=row["id"],
            text=row["text"],
            kind=row["kind"],
            verifier=row["verifier"],
            args=json.loads(row["args"] or "{}"),
            status=row["status"],
            required=bool(row["required"]),
            source=row["source"],
            confidence=row["confidence"],
            contract_id=row["contract_id"],
            dedupe_key=row["dedupe_key"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            meta=json.loads(row["meta"] or "{}"),
        )


@dataclass
class Evidence:
    id: str
    claim_id: str
    verifier: str
    status: str
    summary: str
    details: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    commit_sha: str | None = None
    valid: bool = True
    superseded: bool = False
    invalidated_reason: str | None = None
    created_at: str = field(default_factory=utcnow)
    checked_at: str = field(default_factory=utcnow)

    @property
    def status_enum(self) -> EvidenceStatus:
        return EvidenceStatus(self.status)

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "claim_id": self.claim_id,
            "verifier": self.verifier,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
            "payload": json.dumps(self.payload, sort_keys=True),
            "confidence": self.confidence,
            "commit_sha": self.commit_sha,
            "valid": int(self.valid),
            "invalidated_reason": self.invalidated_reason,
            "created_at": self.created_at,
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_row(cls, row: Any) -> "Evidence":
        return cls(
            id=row["id"],
            claim_id=row["claim_id"],
            verifier=row["verifier"],
            status=row["status"],
            summary=row["summary"],
            details=row["details"] or "",
            payload=json.loads(row["payload"] or "{}"),
            confidence=row["confidence"],
            commit_sha=row["commit_sha"],
            valid=bool(row["valid"]),
            superseded=bool(row["superseded"]) if "superseded" in row.keys() else False,
            invalidated_reason=row["invalidated_reason"],
            created_at=row["created_at"],
            checked_at=row["checked_at"],
        )


@dataclass
class EvidenceResult:
    """What a verifier returns before it is persisted."""

    status: EvidenceStatus
    summary: str
    details: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    confidence: float = 1.0

    def as_evidence(self, claim: Claim, commit_sha: str | None) -> Evidence:
        return Evidence(
            id=new_id("e"),
            claim_id=claim.id,
            verifier=claim.verifier,
            status=self.status.value,
            summary=self.summary,
            details=self.details,
            payload=self.payload,
            confidence=self.confidence,
            commit_sha=commit_sha,
        )


@dataclass
class ClaimDraft:
    """A claim before persistence / verification."""

    text: str
    kind: str = ClaimKind.GENERIC.value
    verifier: str = "none"
    args: dict[str, Any] = field(default_factory=dict)
    required: bool = False
    source: str = "agent"
    confidence: float = 0.5
    contract_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_claim(self) -> Claim:
        return Claim(
            id=new_id("c"),
            text=self.text,
            kind=self.kind,
            verifier=self.verifier,
            args=self.args,
            required=self.required,
            source=self.source,
            confidence=self.confidence,
            contract_id=self.contract_id,
            meta=self.meta,
        )


@dataclass
class DependencyCheck:
    """A precondition discovered while inspecting an agent action."""

    kind: str
    description: str
    satisfied: bool
    risk: Risk = Risk.MEDIUM
    evidence: str = ""
    hint: str = ""


@dataclass
class Decision:
    action: str
    verdict: Verdict
    risk: Risk
    checks: list[DependencyCheck] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def missing(self) -> list[DependencyCheck]:
        return [c for c in self.checks if not c.satisfied]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "verdict": self.verdict.value,
            "risk": self.risk.value,
            "reasons": self.reasons,
            "checks": [
                {
                    "kind": c.kind,
                    "description": c.description,
                    "satisfied": c.satisfied,
                    "risk": c.risk.value,
                    "evidence": c.evidence,
                    "hint": c.hint,
                }
                for c in self.checks
            ],
        }
