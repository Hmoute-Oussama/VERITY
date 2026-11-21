"""Audit and live-agent evidence intake.

External evidence is not inferred from repository automation. Records remain
pending until a report or session is explicitly provided.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def audit_path(root: Path) -> Path:
    return Path(root) / ".verity" / "security-audits.json"


def agent_path(root: Path) -> Path:
    return Path(root) / ".verity" / "agent-sessions.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def audit_evidence(root: Path) -> list[dict[str, Any]]:
    data = _load(audit_path(root))
    reports = data.get("reports")
    return reports if isinstance(reports, list) else []


def agent_evidence(root: Path) -> dict[str, dict[str, Any]]:
    data = _load(agent_path(root))
    sessions = data.get("sessions")
    return sessions if isinstance(sessions, dict) else {}


def add_audit_evidence(
    root: Path,
    *,
    auditor: str,
    version: str,
    report_url: str,
    status: str = "pending",
    commit: str = "",
) -> dict[str, Any]:
    path = audit_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load(path)
    reports = data.get("reports") if isinstance(data.get("reports"), list) else []
    report = {
        "auditor": auditor,
        "version": version,
        "commit": commit,
        "report_url": report_url,
        "status": status,
    }
    reports.append(report)
    path.write_text(json.dumps({"schema": "verity.security-audits.v1", "reports": reports}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def add_agent_evidence(
    root: Path,
    agent: str,
    *,
    version: str,
    os_name: str,
    verity_version: str,
    evidence_url: str,
    result: str = "pending",
) -> dict[str, Any]:
    path = agent_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load(path)
    sessions = data.get("sessions") if isinstance(data.get("sessions"), dict) else {}
    sessions[agent.lower()] = {
        "agent": agent,
        "agent_version": version,
        "os": os_name,
        "verity_version": verity_version,
        "evidence_url": evidence_url,
        "result": result,
    }
    path.write_text(json.dumps({"schema": "verity.agent-sessions.v1", "sessions": sessions}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return sessions[agent.lower()]
