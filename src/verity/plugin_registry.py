"""Local plugin registry and compatibility policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import API_VERSION


def registry_path(root: Path) -> Path:
    return Path(root) / ".verity" / "plugin-registry.json"


def load_registry(root: Path) -> dict[str, Any]:
    path = registry_path(root)
    if not path.is_file():
        return {"schema": "verity.plugin-registry.v1", "plugins": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {"schema": "verity.plugin-registry.v1", "plugins": []}


def register_plugin(
    root: Path,
    *,
    plugin_id: str,
    package: str,
    verifier: str,
    version: str,
    trust_level: str = "community",
    signature_key_id: str = "",
) -> dict[str, Any]:
    data = load_registry(root)
    plugins = data.get("plugins") if isinstance(data.get("plugins"), list) else []
    entry = {
        "id": plugin_id,
        "package": package,
        "verifier": verifier,
        "version": version,
        "minimum_api": API_VERSION,
        "trust_level": trust_level,
        "signature_key_id": signature_key_id,
        "enabled": trust_level in {"verified", "maintainer"},
    }
    plugins = [item for item in plugins if not (isinstance(item, dict) and item.get("id") == plugin_id)]
    plugins.append(entry)
    data["plugins"] = plugins
    path = registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return entry
