from __future__ import annotations

import json
from pathlib import Path

from verity.external_evidence import add_agent_evidence, add_audit_evidence, agent_evidence, audit_evidence
from verity.plugin_registry import load_registry, register_plugin
from verity.release_evidence import release_evidence


def test_external_evidence_remains_explicit(project: Path):
    assert audit_evidence(project) == []
    assert agent_evidence(project) == {}

    add_audit_evidence(
        project,
        auditor="Example Security",
        version="1.0.0",
        report_url="https://example.com/audit",
        status="retest-passed",
        commit="abc123",
    )
    add_agent_evidence(
        project,
        "Claude",
        version="1.0",
        os_name="Linux",
        verity_version="1.0.0",
        evidence_url="https://example.com/session",
        result="passed",
    )
    assert audit_evidence(project)[0]["auditor"] == "Example Security"
    assert agent_evidence(project)["claude"]["result"] == "passed"


def test_plugin_registry_only_enables_verified_plugins(project: Path):
    entry = register_plugin(
        project,
        plugin_id="openapi",
        package="verity-openapi",
        verifier="openapi_policy",
        version="1.0.0",
        trust_level="community",
    )
    assert entry["enabled"] is False

    entry = register_plugin(
        project,
        plugin_id="openapi",
        package="verity-openapi",
        verifier="openapi_policy",
        version="1.0.1",
        trust_level="verified",
    )
    assert entry["enabled"] is True
    assert load_registry(project)["plugins"][0]["id"] == "openapi"


def test_release_evidence_flags_external_claims_as_pending(project: Path):
    evidence = release_evidence(project)
    assert evidence["external_security_audit"]["status"] == "pending"
    assert evidence["live_agent_sessions"]["claude"] == "pending"


def test_public_repository_manifest_tracks_eleventh_published():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "public-repositories.json").read_text(encoding="utf-8"))
    assert len(data["repositories"]) == 11
    dependency = next(item for item in data["repositories"] if item["id"] == "dependency-change")
    assert dependency["status"] == "published"
    assert dependency["url"].endswith("VERITY-integration-dependency-change")
