"""Machine-readable evidence for public release and third-party claims."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import __version__


def release_evidence(root: Path) -> dict[str, Any]:
    return {
        "schema": "verity.release-evidence.v1",
        "version": __version__,
        "repository": "https://github.com/Hmoute-Oussama/VERITY",
        "external_security_audit": {
            "status": "pending",
            "reports": [],
        },
        "live_agent_sessions": {
            "claude": "pending",
            "codex": "pending",
            "opencode": "pending",
            "cursor": "pending",
            "vscode": "pending",
            "windsurf": "pending",
            "gemini": "pending",
        },
        "cross_platform_ci": {
            "linux": "workflow",
            "macos": "workflow",
            "windows": "workflow",
        },
        "package": {
            "distribution": "verity-evidence",
            "pypi": "https://pypi.org/project/verity-evidence/",
        },
    }


def write_release_evidence(root: Path, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(release_evidence(root), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path
