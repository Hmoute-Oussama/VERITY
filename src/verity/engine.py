"""Verification orchestration: analyze -> verify -> invalidate -> report."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .claims.extractor import (
    extract_from_changes,
    extract_from_commands,
    extract_from_text,
    extract_from_transcript,
)
from .config import Config, db_path
from .db import Repository
from .evidence.base import VerifyContext
from .evidence.registry import verify_claim
from .gitutils import Changes, collect_changes, head_sha
from .models import (
    Claim,
    ClaimDraft,
    ClaimStatus,
    Evidence,
    EvidenceStatus,
    utcnow,
)
from .util import digest_files, sha256_file

MAX_CLAIMS_PER_RUN = 400


@dataclass
class Invalidation:
    evidence_id: str
    claim_id: str
    claim_text: str
    path: str | None
    reason: str
    previous_status: str


@dataclass
class ClaimVerification:
    claim: Claim
    evidence: Evidence

    @property
    def status(self) -> EvidenceStatus:
        return EvidenceStatus(self.evidence.status)


@dataclass
class AnalyzeResult:
    claims: list[Claim] = field(default_factory=list)
    new_claims: list[Claim] = field(default_factory=list)
    verifications: list[ClaimVerification] = field(default_factory=list)
    invalidations: list[Invalidation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ProjectStatus:
    project: str
    commit: str | None
    branch: str | None
    counts: dict[str, int]
    claims: list[Claim]
    evidence: list[Evidence]
    invalidations: list[Invalidation]
    contracts_met: int = 0
    contracts_total: int = 0
    overall: str = "incomplete"

    @property
    def total(self) -> int:
        return len(self.claims)

    def claims_with(self, status: str) -> list[Claim]:
        return [c for c in self.claims if c.status == status]


def compute_claim_status(evidence: list[Evidence]) -> ClaimStatus:
    if not evidence:
        return ClaimStatus.UNVERIFIED
    valid = [e for e in evidence if e.valid]
    if not valid:
        return ClaimStatus.STALE
    if any(e.status in {EvidenceStatus.FAIL.value, EvidenceStatus.ERROR.value} for e in valid):
        return ClaimStatus.CONTRADICTED
    passes = [e for e in valid if e.status == EvidenceStatus.PASS.value]
    warns = [e for e in valid if e.status == EvidenceStatus.WARN.value]
    if passes and not warns:
        return ClaimStatus.VERIFIED
    if passes:
        return ClaimStatus.PARTIALLY_VERIFIED
    return ClaimStatus.UNVERIFIED


class Engine:
    def __init__(
        self,
        root: Path | str,
        *,
        config: Config | None = None,
        repo: Repository | None = None,
        changes: Changes | None = None,
    ):
        self.root = Path(root).resolve()
        self.config = config or Config.load(self.root)
        self.repo = repo or Repository(
            db_path(self.root), self.config.project or self.root.name
        )
        self.changes = changes if changes is not None else collect_changes(self.root)
        self.commit = self.changes.commit if self.changes.is_repo else None

    # ------------------------------------------------------------------ context
    def context(self) -> VerifyContext:
        return VerifyContext(
            root=self.root,
            config=self.config,
            commit=self.commit,
            changes=self.changes,
        )

    def refresh(self) -> None:
        self.changes = collect_changes(self.root)
        self.commit = self.changes.commit if self.changes.is_repo else None

    # ------------------------------------------------------------------ analyze
    def analyze(
        self,
        *,
        text: str | None = None,
        transcript: Path | None = None,
        commands: Iterable[str] | None = None,
        use_diff: bool = True,
        verify: bool = True,
        source: str = "agent",
    ) -> AnalyzeResult:
        result = AnalyzeResult()
        drafts: list[ClaimDraft] = []
        if text:
            drafts.extend(extract_from_text(text, source=source))
        if transcript:
            drafts.extend(extract_from_transcript(transcript))
        if commands:
            drafts.extend(extract_from_commands(commands))
        if use_diff and self.changes.is_dirty:
            drafts.extend(extract_from_changes(self.changes, self.root))
        if not drafts:
            result.notes.append("No verifiable claims were found in the provided input.")
            return result

        seen: set[str] = set()
        for draft in drafts:
            existing = self.repo.find_by_dedupe_key(draft.to_claim().dedupe_key)
            claim = self.repo.upsert_claim(draft)
            result.claims.append(claim)
            if existing is None:
                result.new_claims.append(claim)
            seen.add(claim.dedupe_key)

        if verify:
            targets = [c for c in result.claims if c.verifier != "none" or c.required]
            result.verifications = self.verify_claims(targets)
            # Claims with no automated verifier still get an explicit
            # "assumption" evidence record, so they cannot silently disappear.
            assumptions = [c for c in result.claims if c.verifier == "none" and not c.required]
            result.verifications.extend(self.verify_claims(assumptions))
        self.repo.record_event(
            "analyze",
            {
                "claims": len(result.claims),
                "new": len(result.new_claims),
                "source": source,
            },
        )
        return result

    def add_claim(self, draft: ClaimDraft, *, verify: bool = False) -> tuple[Claim, list[ClaimVerification]]:
        existing = self.repo.find_by_dedupe_key(draft.to_claim().dedupe_key)
        claim = self.repo.upsert_claim(draft)
        verifications: list[ClaimVerification] = []
        if verify:
            verifications = self.verify_claims([claim])
        self.repo.record_event("claim_added", {"claim_id": claim.id, "new": existing is None})
        return claim, verifications

    # ------------------------------------------------------------------ verify
    def verify_claims(
        self, claims: Iterable[Claim], *, only_stale: bool = False
    ) -> list[ClaimVerification]:
        ctx = self.context()
        out: list[ClaimVerification] = []
        for claim in list(claims)[:MAX_CLAIMS_PER_RUN]:
            if only_stale and claim.status not in {
                ClaimStatus.STALE.value,
                ClaimStatus.UNVERIFIED.value,
            }:
                continue
            result = verify_claim(claim, ctx)
            deps = digest_files(self.root, result.dependencies)
            evidence = self.repo.add_evidence(claim, result, self.commit, deps)
            status = compute_claim_status(self.repo.evidence_for(claim.id))
            self.repo.set_claim_status(claim.id, status)
            claim.status = status.value
            claim.updated_at = utcnow()
            out.append(ClaimVerification(claim=claim, evidence=evidence))
        return out

    def verify(self, claim_ids: Iterable[str] | None = None, *, only_stale: bool = False) -> list[ClaimVerification]:
        if claim_ids:
            claims = [c for c in (self.repo.get_claim(cid) for cid in claim_ids) if c]
        else:
            claims = self.repo.claims()
        return self.verify_claims(claims, only_stale=only_stale)

    def verify_required(self) -> list[ClaimVerification]:
        return self.verify_claims(self.repo.claims(required_only=True))

    # ------------------------------------------------------------------ decay
    def invalidate_stale(self, *, dry_run: bool = False) -> list[Invalidation]:
        invalidations: list[Invalidation] = []
        digest_cache: dict[str, str | None] = {}
        now = datetime.now(timezone.utc)
        ttl = self.config.evidence.ttl_seconds

        for evidence in self.repo.all_evidence(current_only=True, valid_only=True):
            deps = self.repo.evidence_deps(evidence.id)
            reason: str | None = None
            path: str | None = None

            for dep_path, stored in deps.items():
                if dep_path not in digest_cache:
                    digest_cache[dep_path] = sha256_file(self.root / dep_path)
                current = digest_cache[dep_path]
                if current != stored:
                    path = dep_path
                    if current is None:
                        reason = f"dependency `{dep_path}` was deleted or is unreadable"
                    else:
                        reason = f"dependency `{dep_path}` changed after verification"
                    break

            if reason is None and ttl and not deps:
                try:
                    checked = datetime.fromisoformat(evidence.checked_at)
                    if checked.tzinfo is None:
                        checked = checked.replace(tzinfo=timezone.utc)
                    age = (now - checked).total_seconds()
                    if age > ttl:
                        reason = f"evidence exceeded its TTL of {ttl}s"
                except ValueError:
                    pass

            if reason is None:
                if deps and not dry_run:
                    self.repo.touch_evidence(evidence.id)
                continue

            claim = self.repo.get_claim(evidence.claim_id)
            inv = Invalidation(
                evidence_id=evidence.id,
                claim_id=evidence.claim_id,
                claim_text=claim.text if claim else "(deleted claim)",
                path=path,
                reason=reason,
                previous_status=evidence.status,
            )
            invalidations.append(inv)
            if not dry_run:
                self.repo.invalidate_evidence(evidence.id, reason)
                if claim:
                    status = compute_claim_status(self.repo.evidence_for(claim.id))
                    self.repo.set_claim_status(claim.id, status)

        if invalidations and not dry_run:
            self.repo.record_event(
                "evidence_invalidated",
                {
                    "count": len(invalidations),
                    "paths": sorted({i.path for i in invalidations if i.path}),
                },
            )
        return invalidations

    def refresh_statuses(self) -> None:
        for claim in self.repo.claims():
            status = compute_claim_status(self.repo.evidence_for(claim.id))
            if status.value != claim.status:
                self.repo.set_claim_status(claim.id, status)

    # ------------------------------------------------------------------ status
    def status(self) -> ProjectStatus:
        invalidations = self.invalidate_stale()
        claims = self.repo.claims()
        evidence = self.repo.all_evidence(current_only=True)
        counts = {s.value: 0 for s in ClaimStatus}
        for claim in claims:
            counts[claim.status] = counts.get(claim.status, 0) + 1
        required = [c for c in claims if c.required]
        met = sum(1 for c in required if c.status == ClaimStatus.VERIFIED.value)
        contract_claims = [c for c in claims if c.contract_id]
        contract_ids = {c.contract_id for c in contract_claims}
        contracts_met = len(
            {c.contract_id for c in contract_claims if c.status == ClaimStatus.VERIFIED.value}
        )
        overall = _overall(claims, required)
        return ProjectStatus(
            project=self.repo.project,
            commit=self.commit,
            branch=_branch(self.root),
            counts=counts,
            claims=claims,
            evidence=evidence,
            invalidations=invalidations,
            contracts_met=contracts_met,
            contracts_total=len(contract_ids),
            overall=overall,
        )


def _overall(claims: list[Claim], required: list[Claim]) -> str:
    if not claims:
        return "no-claims"
    if any(c.status == ClaimStatus.CONTRADICTED.value for c in claims):
        return "contradicted"
    if any(c.status == ClaimStatus.STALE.value for c in claims):
        return "stale"
    if required and all(c.status == ClaimStatus.VERIFIED.value for c in required):
        return "verified"
    if all(c.status == ClaimStatus.VERIFIED.value for c in claims):
        return "verified"
    return "incomplete"


def _branch(root: Path) -> str | None:
    from .gitutils import branch

    return branch(root)
