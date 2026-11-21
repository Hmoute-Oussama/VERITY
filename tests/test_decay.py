from __future__ import annotations

from verity.models import ClaimDraft


def test_evidence_is_invalidated_when_dependency_changes(engine):
    draft = ClaimDraft(
        text="authenticate_user exists",
        verifier="code_symbol",
        args={"symbol": "authenticate_user"},
    )
    claim, verifications = engine.add_claim(draft, verify=True)
    assert verifications[0].evidence.status == "pass"
    assert engine.repo.get_claim(claim.id).status == "verified"

    (engine.root / "app.py").write_text("def something_else():\n    pass\n")
    invalidations = engine.invalidate_stale()

    assert len(invalidations) == 1
    assert invalidations[0].path == "app.py"
    assert "changed after verification" in invalidations[0].reason
    assert engine.repo.get_claim(claim.id).status == "stale"


def test_invalidation_is_idempotent(engine):
    draft = ClaimDraft(text="app.py exists", verifier="file_exists", args={"paths": ["app.py"]})
    claim, _ = engine.add_claim(draft, verify=True)
    (engine.root / "app.py").write_text("x = 1\n")
    assert len(engine.invalidate_stale()) == 1
    assert engine.invalidate_stale() == []
    assert engine.repo.get_claim(claim.id).status == "stale"


def test_dry_run_does_not_mutate(engine):
    draft = ClaimDraft(text="app.py exists", verifier="file_exists", args={"paths": ["app.py"]})
    claim, _ = engine.add_claim(draft, verify=True)
    (engine.root / "app.py").write_text("x = 2\n")
    invalidations = engine.invalidate_stale(dry_run=True)
    assert len(invalidations) == 1
    assert engine.repo.get_claim(claim.id).status == "verified"
    assert engine.repo.evidence_for(claim.id)[0].valid is True


def test_reverification_restores_verified_status(engine):
    draft = ClaimDraft(
        text="authenticate_user exists",
        verifier="code_symbol",
        args={"symbol": "authenticate_user"},
    )
    claim, _ = engine.add_claim(draft, verify=True)
    (engine.root / "app.py").write_text(
        "def authenticate_user(token: str) -> dict:\n    return {'ok': True}\n"
    )
    engine.invalidate_stale()
    assert engine.repo.get_claim(claim.id).status == "stale"

    engine.verify(claim_ids=[claim.id])
    assert engine.repo.get_claim(claim.id).status == "verified"


def test_missing_symbol_evidence_has_no_dependencies(engine):
    draft = ClaimDraft(
        text="missing symbol exists",
        verifier="code_symbol",
        args={"symbol": "missing_symbol"},
    )
    claim, verifications = engine.add_claim(draft, verify=True)
    assert verifications[0].evidence.status == "fail"
    assert engine.repo.evidence_deps(verifications[0].evidence.id) == {}
    assert engine.repo.get_claim(claim.id).status == "contradicted"


def test_new_evidence_supersedes_old(engine):
    draft = ClaimDraft(text="app.py exists", verifier="file_exists", args={"paths": ["app.py"]})
    claim, _ = engine.add_claim(draft, verify=True)
    engine.verify(claim_ids=[claim.id])
    current = engine.repo.evidence_for(claim.id)
    assert len(current) == 1
    assert len(engine.repo.evidence_history(claim.id)) == 2
    assert sum(1 for e in engine.repo.evidence_history(claim.id) if e.superseded) == 1


def test_status_reports_invalidations(engine):
    draft = ClaimDraft(text="app.py exists", verifier="file_exists", args={"paths": ["app.py"]})
    engine.add_claim(draft, verify=True)
    (engine.root / "app.py").write_text("changed = True\n")
    status = engine.status()
    assert status.counts["stale"] >= 1
    assert status.invalidations
    assert status.overall == "stale"


def test_contract_claims_are_counted_in_status(engine):
    from verity.contracts import check_contracts, load_contracts

    (engine.root / ".verity" / "contracts.yaml").write_text(
        "version: 1\ntask: t\ncontracts:\n"
        "  - id: symbol\n    claim: authenticate_user exists\n"
        "    verifier: code_symbol\n    args: {symbol: authenticate_user}\n"
    )
    check_contracts(engine, load_contracts(engine.root))
    status = engine.status()
    assert status.contracts_total == 1
    assert status.contracts_met == 1
