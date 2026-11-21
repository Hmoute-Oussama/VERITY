"""Stable, versioned JSON output helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import API_VERSION, __version__


def json_envelope(command: str, data: Any) -> dict[str, Any]:
    return {
        "schema": "verity.output.v1",
        "api_version": API_VERSION,
        "verity_version": __version__,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "command": command,
        "data": data,
    }


def dumps_versioned(command: str, data: Any) -> str:
    return json.dumps(json_envelope(command, data), sort_keys=True, indent=2)


def junit_xml(results: list[dict[str, Any]]) -> str:
    failures = sum(1 for item in results if item.get("status") in {"fail", "error", "contradicted"})
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<testsuite name="verity" tests="{len(results)}" failures="{failures}">',
    ]
    for item in results:
        name = str(item.get("claim") or item.get("id") or "verity")
        status = str(item.get("status") or "unverified")
        lines.append(f'  <testcase name="{name.replace("&", "&amp;").replace(chr(34), "&quot;")}" status="{status}">')
        if status in {"fail", "error", "contradicted"}:
            lines.append(f"    <failure message=\"VERITY evidence {status}\" />")
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return "\n".join(lines)


def sarif_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "VERITY", "informationUri": "https://github.com/Hmoute-Oussama/VERITY", "rules": []}},
            "results": [
                {
                    "ruleId": "verity/evidence",
                    "level": "error" if item.get("status") in {"fail", "error", "contradicted"} else "warning",
                    "message": {"text": str(item.get("claim") or item.get("summary") or "VERITY evidence result")},
                }
                for item in results
            ],
        }],
    }
