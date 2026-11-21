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
