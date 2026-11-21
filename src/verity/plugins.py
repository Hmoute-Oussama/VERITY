"""Lightweight plugin discovery for community verifier extensions.

Plugins are declarative manifests; VERITY does not execute plugin Python by
default. This prevents third-party packages from gaining command execution
privileges merely by existing in a project.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import load_yaml


@dataclass(frozen=True)
class PluginDescriptor:
    id: str
    name: str
    description: str
    package: str
    verifier: str
    verified: bool = False
    minimum_api: str = "1.0"
    trust_level: str = "community"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "package": self.package,
            "verifier": self.verifier,
            "verified": self.verified,
            "minimum_api": self.minimum_api,
            "trust_level": self.trust_level,
        }


def discover_plugins(root: Path) -> list[PluginDescriptor]:
    path = Path(root) / ".verity" / "plugins.yaml"
    if not path.is_file():
        return []
    data = load_yaml(path)
    if not isinstance(data, dict):
        return []
    raw = data.get("plugins") or []
    out: list[PluginDescriptor] = []
    if isinstance(raw, dict):
        items = [
            {"id": str(identifier), **(spec if isinstance(spec, dict) else {})}
            for identifier, spec in raw.items()
        ]
    elif isinstance(raw, list):
        items = [item for item in raw if isinstance(item, dict)]
    else:
        items = []
    for item in items:
        identifier = str(item.get("id") or "").strip()
        verifier = str(item.get("verifier") or "").strip()
        if not identifier or not verifier:
            continue
        out.append(
            PluginDescriptor(
                id=identifier,
                name=str(item.get("name") or identifier),
                description=str(item.get("description") or ""),
                package=str(item.get("package") or ""),
                verifier=verifier,
                verified=bool(item.get("verified", False)),
                minimum_api=str(item.get("minimum_api") or "1.0"),
                trust_level=str(item.get("trust_level") or ("verified" if item.get("verified") else "community")),
            )
        )
    return out
