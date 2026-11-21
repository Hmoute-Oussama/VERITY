"""Verifier protocol and shared helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..config import Config
from ..gitutils import Changes
from ..models import Claim, EvidenceResult, EvidenceStatus
from ..util import digest_files, relpath


@dataclass
class VerifyContext:
    root: Path
    config: Config
    commit: str | None = None
    changes: Changes | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def deps(self, paths: list[str]) -> dict[str, str | None]:
        return digest_files(self.root, paths)

    def rel(self, path: Path | str) -> str:
        return relpath(Path(path), self.root)


class Verifier(Protocol):
    name: str

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult: ...


def ok(summary: str, *, details: str = "", payload: dict[str, Any] | None = None,
       deps: list[str] | None = None, confidence: float = 1.0) -> EvidenceResult:
    return EvidenceResult(
        status=EvidenceStatus.PASS,
        summary=summary,
        details=details,
        payload=payload or {},
        dependencies=deps or [],
        confidence=confidence,
    )


def warn(summary: str, *, details: str = "", payload: dict[str, Any] | None = None,
         deps: list[str] | None = None, confidence: float = 0.5) -> EvidenceResult:
    return EvidenceResult(
        status=EvidenceStatus.WARN,
        summary=summary,
        details=details,
        payload=payload or {},
        dependencies=deps or [],
        confidence=confidence,
    )


def fail(summary: str, *, details: str = "", payload: dict[str, Any] | None = None,
         deps: list[str] | None = None, confidence: float = 0.9) -> EvidenceResult:
    return EvidenceResult(
        status=EvidenceStatus.FAIL,
        summary=summary,
        details=details,
        payload=payload or {},
        dependencies=deps or [],
        confidence=confidence,
    )


def error(summary: str, *, details: str = "", payload: dict[str, Any] | None = None) -> EvidenceResult:
    return EvidenceResult(
        status=EvidenceStatus.ERROR,
        summary=summary,
        details=details,
        payload=payload or {},
        dependencies=[],
        confidence=0.0,
    )
