"""File, configuration and manifest-path verifiers."""

from __future__ import annotations

import configparser
import json
import re
from pathlib import Path
from typing import Any

from ..models import Claim, EvidenceResult
from ..util import glob_paths, load_toml, read_text, relpath
from .base import VerifyContext, fail, ok, warn


class FileExistsVerifier:
    name = "file_exists"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        paths = [str(p) for p in (claim.args.get("paths") or claim.args.get("path") or [])]
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            return fail("No path was specified for this claim")
        expect = claim.args.get("expect", "present")

        found: list[str] = []
        checked: list[str] = []
        for pattern in paths:
            for path in glob_paths(ctx.root, pattern):
                if path.exists():
                    rel = relpath(path, ctx.root)
                    if rel not in found:
                        found.append(rel)
            checked.append(pattern)

        if expect == "absent":
            if found:
                return fail(
                    f"Path still exists: {', '.join(found[:5])}",
                    details="The claim asserted this path should be absent.",
                    payload={"found": found},
                    deps=found,
                )
            return ok(
                "None of the expected-absent paths exist",
                details="Checked: " + ", ".join(checked),
                payload={"checked": checked},
            )

        if found:
            return ok(
                f"Found {len(found)} matching path(s)",
                details=", ".join(found[:10]),
                payload={"found": found, "checked": checked},
                deps=found,
            )
        return fail(
            "No matching file exists",
            details="Checked: " + ", ".join(checked),
            payload={"checked": checked},
            confidence=0.98,
        )


class ConfigValidVerifier:
    name = "config_valid"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        paths = [str(p) for p in (claim.args.get("paths") or claim.args.get("path") or [])]
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            paths = [
                "compose.yaml",
                "compose.yml",
                "docker-compose.yml",
                "docker-compose.yaml",
            ]
        files: list[Path] = []
        for pattern in paths:
            files.extend(p for p in glob_paths(ctx.root, pattern) if p.is_file())
        if not files:
            return fail(
                "No configuration files were found",
                details="Checked: " + ", ".join(paths),
                payload={"checked": paths},
            )

        errors: list[str] = []
        valid: list[str] = []
        deps: list[str] = []
        for path in files:
            rel = relpath(path, ctx.root)
            deps.append(rel)
            try:
                _parse_config(path)
                valid.append(rel)
            except Exception as exc:
                errors.append(f"{rel}: {type(exc).__name__}: {exc}")
        if errors:
            return fail(
                f"{len(errors)} configuration file(s) are invalid",
                details="\n".join(errors[:20]),
                payload={"errors": errors[:50], "valid": valid},
                deps=deps,
            )
        return ok(
            f"{len(valid)} configuration file(s) parse correctly",
            details=", ".join(valid[:10]),
            payload={"valid": valid},
            deps=deps,
        )


def _parse_config(path: Path) -> Any:
    suffix = path.suffix.lower()
    text = read_text(path)
    if suffix in {".yaml", ".yml"}:
        import yaml

        return yaml.safe_load(text)
    if suffix == ".json":
        return json.loads(text)
    if suffix == ".toml":
        import tomllib

        return tomllib.loads(text)
    if suffix in {".ini", ".cfg"}:
        parser = configparser.ConfigParser()
        parser.read_string(text)
        return dict(parser)
    if path.name.startswith(".env") or suffix == ".env":
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                raise ValueError(f"line {i}: expected KEY=VALUE")
        return True
    raise ValueError(f"unsupported configuration format: {suffix or path.name}")


class JsonPathVerifier:
    name = "json_path"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        rel = str(claim.args.get("path") or "")
        json_path = str(claim.args.get("json_path") or "").strip()
        if not rel:
            return fail("No JSON file was specified")
        path = ctx.root / rel
        if not path.is_file():
            return fail(f"`{rel}` does not exist", payload={"path": rel})
        try:
            data = json.loads(read_text(path))
        except json.JSONDecodeError as exc:
            return fail(
                f"`{rel}` is not valid JSON",
                details=f"line {exc.lineno}: {exc.msg}",
                deps=[rel],
            )
        if not json_path:
            return ok(f"`{rel}` is valid JSON", deps=[rel])
        current: Any = data
        traversed: list[str] = []
        for part in json_path.split("."):
            traversed.append(part)
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                return fail(
                    f"`{rel}` has no `{json_path}`",
                    details="Resolved: " + ".".join(traversed[:-1]) + " (missing: " + part + ")",
                    payload={"path": rel, "json_path": json_path, "resolved": ".".join(traversed[:-1])},
                    deps=[rel],
                )
        rendered = current if isinstance(current, (str, int, float, bool)) else type(current).__name__
        return ok(
            f"`{json_path}` is defined in `{rel}`",
            details=f"Value: {str(rendered)[:200]}",
            payload={"path": rel, "json_path": json_path, "value_type": type(current).__name__},
            deps=[rel],
        )
