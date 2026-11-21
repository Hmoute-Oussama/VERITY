"""Local trust-store support for signed evidence bundles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def trust_dir(root: Path) -> Path:
    return Path(root) / ".verity" / "trust"


def trust_store(root: Path) -> Path:
    return trust_dir(root) / "keys.json"


def public_key_id(public_key_pem: str) -> str:
    return hashlib.sha256(public_key_pem.encode("utf-8")).hexdigest()[:32]


def load_trusted_keys(root: Path) -> dict[str, dict[str, Any]]:
    path = trust_store(root)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    keys = data.get("keys")
    return keys if isinstance(keys, dict) else {}


def add_trusted_key(root: Path, name: str, public_key_pem: str) -> str:
    path = trust_store(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    key_id = public_key_id(public_key_pem)
    data: dict[str, Any] = {"version": 1, "keys": load_trusted_keys(root)}
    data["keys"][key_id] = {
        "name": name,
        "key_id": key_id,
        "public_key_pem": public_key_pem,
        "status": "trusted",
    }
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return key_id


def remove_trusted_key(root: Path, key_id: str) -> bool:
    path = trust_store(root)
    if not path.is_file():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    keys = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(keys, dict) or key_id not in keys:
        return False
    del keys[key_id]
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return True
