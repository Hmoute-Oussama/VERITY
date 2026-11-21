"""Reality snapshots: compact, current, machine-readable project state."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .contracts import contract_summary, load_contracts
from .engine import Engine, ProjectStatus
from .models import ClaimStatus, EvidenceStatus

STATUS_ICON = {
    "verified": "[PASS]",
    "partially_verified": "[PART]",
    "unverified": "[OPEN]",
    "contradicted": "[FAIL]",
    "stale": "[STALE]",
}


def build_snapshot(status: ProjectStatus, *, compact: bool = False) -> str:
    if compact:
        return _compact(status)
    lines: list[str] = []
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    lines.append("# VERITY Reality Snapshot")
    lines.append("")
    lines.append(f"- **Project:** {status.project}")
    lines.append(f"- **Commit:** `{status.commit or 'no commit'}`" + (f" on `{status.branch}`" if status.branch else ""))
    lines.append(f"- **Generated:** {generated}")
    lines.append(f"- **Overall:** **{status.overall}**")
    lines.append("")

    verified = status.claims_with(ClaimStatus.VERIFIED.value)
    partial = status.claims_with(ClaimStatus.PARTIALLY_VERIFIED.value)
    unverified = status.claims_with(ClaimStatus.UNVERIFIED.value)
    contradicted = status.claims_with(ClaimStatus.CONTRADICTED.value)
    stale = status.claims_with(ClaimStatus.STALE.value)

    lines.append("## Verified facts")
    if verified:
        for claim in verified:
            evidence = _current_evidence(status, claim.id)
            summary = evidence.summary if evidence else ""
            lines.append(f"- **{claim.text}**")
            if summary:
                lines.append(f"  - evidence: {summary}")
    else:
        lines.append("- _None. No claim has current, passing evidence._")
    lines.append("")

    lines.append("## Unverified assumptions")
    assumptions = unverified + partial
    if assumptions:
        for claim in assumptions:
            evidence = _current_evidence(status, claim.id)
            note = f" — {evidence.summary}" if evidence else ""
            lines.append(f"- {claim.text} _({claim.status.replace('_', ' ')}){note}_")
    else:
        lines.append("- _None._")
    lines.append("")

    lines.append("## Contradicted claims")
    if contradicted:
        for claim in contradicted:
            evidence = _current_evidence(status, claim.id)
            detail = f" — {evidence.summary}" if evidence else ""
            lines.append(f"- {claim.text}{detail}")
    else:
        lines.append("- _None._")
    lines.append("")

    lines.append("## Invalidated evidence (requires re-verification)")
    invalidated = [e for e in status.evidence if not e.valid]
    if invalidated or stale:
        for claim in stale:
            lines.append(f"- {claim.text} — evidence is stale")
        for evidence in invalidated:
            claim = next((c for c in status.claims if c.id == evidence.claim_id), None)
            label = claim.text if claim else evidence.claim_id
            lines.append(f"- {label} — {evidence.invalidated_reason or 'invalidated'}")
    else:
        lines.append("- _None._")
    lines.append("")

    lines.append("## Evidence contracts")
    if status.contracts_total:
        lines.append(f"- **{status.contracts_met}/{status.contracts_total}** required contracts satisfied")
        for claim in status.claims:
            if claim.contract_id:
                icon = STATUS_ICON.get(claim.status, "[?]")
                lines.append(f"- {icon} `{claim.contract_id}` — {claim.text}")
    else:
        lines.append("- _No evidence contracts defined. Run `verity contract init`._")
    lines.append("")

    lines.append("## Next actions")
    if contradicted:
        lines.append("- Fix contradicted claims, then run `verity verify`.")
    if stale:
        lines.append("- Re-verify stale claims with `verity verify --stale`.")
    if unverified or partial:
        lines.append("- Define contracts or attach verifiers for unverified claims.")
    if not status.claims:
        lines.append("- Run `verity analyze \"<agent statement>\" --diff` to extract claims.")
    if not status.claims or status.overall == "verified":
        lines.append("- Nothing blocking. Continue with `verity snapshot --write`.")
    lines.append("")
    return "\n".join(lines)


def _compact(status: ProjectStatus) -> str:
    lines = [
        f"VERITY reality — {status.project} @ {(status.commit or 'no-commit')[:7]}",
        f"Overall: {status.overall}",
    ]
    verified = status.claims_with(ClaimStatus.VERIFIED.value)
    contradicted = status.claims_with(ClaimStatus.CONTRADICTED.value)
    stale = status.claims_with(ClaimStatus.STALE.value)
    unverified = status.claims_with(ClaimStatus.UNVERIFIED.value)
    partial = status.claims_with(ClaimStatus.PARTIALLY_VERIFIED.value)

    def section(title: str, claims: list, limit: int = 12) -> None:
        if not claims:
            return
        lines.append(f"{title} ({len(claims)}):")
        for claim in claims[:limit]:
            suffix = ""
            if claim.contract_id:
                suffix = f" [{claim.contract_id}]"
            lines.append(f"- {claim.text}{suffix}")
        if len(claims) > limit:
            lines.append(f"- … {len(claims) - limit} more")

    section("VERIFIED", verified)
    section("CONTRADICTED", contradicted)
    section("STALE", stale)
    section("ASSUMPTIONS", unverified + partial)
    if status.contracts_total:
        lines.append(f"CONTRACTS: {status.contracts_met}/{status.contracts_total} satisfied")
    return "\n".join(lines)


def _current_evidence(status: ProjectStatus, claim_id: str):
    for evidence in status.evidence:
        if evidence.claim_id == claim_id and evidence.valid:
            return evidence
    return None


def write_snapshot(engine: Engine, path: Path) -> Path:
    status = engine.status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_snapshot(status), encoding="utf-8")
    engine.repo.record_event("snapshot_written", {"path": str(path)})
    return path


def build_agent_context(engine: Engine) -> dict:
    status = engine.status()
    return {
        "project": status.project,
        "commit": status.commit,
        "branch": status.branch,
        "overall": status.overall,
        "counts": status.counts,
        "contracts": {"satisfied": status.contracts_met, "total": status.contracts_total},
        "verified": [
            {"id": c.id, "claim": c.text, "contract": c.contract_id}
            for c in status.claims_with(ClaimStatus.VERIFIED.value)
        ],
        "unverified": [
            {"id": c.id, "claim": c.text, "status": c.status, "contract": c.contract_id}
            for c in status.claims_with(ClaimStatus.UNVERIFIED.value)
            + status.claims_with(ClaimStatus.PARTIALLY_VERIFIED.value)
        ],
        "contradicted": [
            {"id": c.id, "claim": c.text, "contract": c.contract_id}
            for c in status.claims_with(ClaimStatus.CONTRADICTED.value)
        ],
        "stale": [
            {"id": c.id, "claim": c.text, "contract": c.contract_id}
            for c in status.claims_with(ClaimStatus.STALE.value)
        ],
        "snapshot_markdown": build_snapshot(status, compact=True),
    }
