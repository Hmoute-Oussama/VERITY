from __future__ import annotations

from verity.models import ClaimDraft, EvidenceResult, EvidenceStatus


def test_upsert_claim_dedupes_identical_drafts(engine):
    draft = ClaimDraft(
        text="File `app.py` exists",
        verifier="file_exists",
        args={"paths": ["app.py"]},
    )

    first = engine.repo.upsert_claim(draft)
    second = engine.repo.upsert_claim(draft)

    assert first.id == second.id
    assert len(engine.repo.claims()) == 1


def test_evidence_history_marks_previous_as_superseded(engine):
    claim = engine.repo.upsert_claim(
        ClaimDraft(text="x", verifier="file_exists", args={"paths": ["app.py"]})
    )
    first = engine.repo.add_evidence(
        claim,
        EvidenceResult(status=EvidenceStatus.PASS, summary="first"),
        commit_sha=None,
        deps={"app.py": "digest-1"},
    )
    second = engine.repo.add_evidence(
        claim,
        EvidenceResult(status=EvidenceStatus.FAIL, summary="second"),
        commit_sha=None,
        deps={"app.py": "digest-2"},
    )

    current = engine.repo.evidence_for(claim.id)
    history = engine.repo.evidence_history(claim.id)

    assert [e.id for e in current] == [second.id]
    assert {e.id for e in history} == {first.id, second.id}
    assert engine.repo.evidence_deps(second.id) == {"app.py": "digest-2"}


def test_events_are_recorded(engine):
    engine.repo.record_event("test_event", {"answer": 42})

    events = engine.repo.events()
    assert events[0]["type"] == "test_event"
    assert events[0]["payload"] == {"answer": 42}


def test_database_records_current_schema_version(engine):
    assert engine.repo.schema_version == 1
    assert engine.repo._query(
        "SELECT value FROM meta WHERE key = 'db_schema_version'"
    )[0]["value"] == "1"


def test_database_rejects_newer_schema_version(project):
    from verity.db import Repository

    repo = Repository(project / ".verity" / "verity.db", "test-project")
    repo.set_meta("db_schema_version", "999")
    repo.close()

    try:
        Repository(project / ".verity" / "verity.db", "test-project")
    except RuntimeError as exc:
        assert "newer than supported" in str(exc)
    else:
        raise AssertionError("A newer database schema must be rejected")


def test_delete_claim(engine):
    claim = engine.repo.upsert_claim(ClaimDraft(text="x", verifier="none"))

    assert engine.repo.delete_claim(claim.id) is True
    assert engine.repo.get_claim(claim.id) is None
    assert engine.repo.delete_claim(claim.id) is False
