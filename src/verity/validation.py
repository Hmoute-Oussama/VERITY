"""Friendly validation for VERITY YAML configuration and contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import contracts_path
from .util import load_yaml


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


def validate_config(root: Path) -> list[ValidationIssue]:
    path = Path(root) / ".verity" / "config.yaml"
    if not path.is_file():
        return [ValidationIssue(str(path), "configuration file does not exist")]
    data = load_yaml(path)
    if not isinstance(data, dict):
        return [ValidationIssue(str(path), "configuration must be a YAML mapping")]
    issues: list[ValidationIssue] = []
    for section in ("verification", "evidence", "policy"):
        value = data.get(section)
        if value is not None and not isinstance(value, dict):
            issues.append(ValidationIssue(section, f"{section} must be a mapping"))
    verification = data.get("verification")
    if isinstance(verification, dict):
        timeout = verification.get("timeout_seconds")
        if timeout is not None and (not isinstance(timeout, int) or timeout < 1):
            issues.append(ValidationIssue("verification.timeout_seconds", "must be a positive integer"))
        allow_exec = verification.get("allow_exec")
        if allow_exec is not None and not isinstance(allow_exec, bool):
            issues.append(ValidationIssue("verification.allow_exec", "must be true or false"))
    policy = data.get("policy")
    if isinstance(policy, dict):
        for key in ("deny_commands", "allow_commands"):
            value = policy.get(key)
            if value is not None and not isinstance(value, list):
                issues.append(ValidationIssue(f"policy.{key}", "must be a list"))
    return issues


def validate_contracts(root: Path) -> list[ValidationIssue]:
    path = contracts_path(Path(root))
    if not path.is_file():
        return [ValidationIssue(str(path), "contracts file does not exist")]
    data = load_yaml(path)
    if not isinstance(data, dict):
        return [ValidationIssue(str(path), "contracts must be a YAML mapping")]
    contracts: Any = data.get("contracts")
    if contracts is None:
        return [ValidationIssue("contracts", "missing required contracts list or mapping")]
    if isinstance(contracts, dict):
        items = list(contracts.items())
    elif isinstance(contracts, list):
        items = [(str(i), item) for i, item in enumerate(contracts)]
    else:
        return [ValidationIssue("contracts", "must be a list or mapping")]
    issues: list[ValidationIssue] = []
    for identifier, item in items:
        prefix = f"contracts.{identifier}"
        if not isinstance(item, dict):
            issues.append(ValidationIssue(prefix, "contract must be a mapping"))
            continue
        claim = item.get("claim") or item.get("description")
        if not isinstance(claim, str) or not claim.strip():
            issues.append(ValidationIssue(prefix + ".claim", "required non-empty string"))
        verifier = item.get("verifier")
        if not isinstance(verifier, str) or not verifier.strip():
            issues.append(ValidationIssue(prefix + ".verifier", "required non-empty string"))
        args = item.get("args")
        if args is not None and not isinstance(args, dict):
            issues.append(ValidationIssue(prefix + ".args", "must be a mapping"))
        required = item.get("required")
        if required is not None and not isinstance(required, bool):
            issues.append(ValidationIssue(prefix + ".required", "must be true or false"))
    return issues
