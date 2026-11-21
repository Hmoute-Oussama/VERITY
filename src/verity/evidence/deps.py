"""Dependency verifier: manifests first, installed environment second."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

from ..models import Claim, EvidenceResult
from ..util import (
    load_json,
    load_toml,
    normalize_package,
    read_text,
    relpath,
    walk_files,
)
from .base import VerifyContext, fail, ok, warn

STDLIB = set(getattr(sys, "stdlib_module_names", set()))

# distribution name -> import name (only where they differ)
IMPORT_NAMES = {
    "pyjwt": "jwt",
    "python-jose": "jose",
    "psycopg2-binary": "psycopg2",
    "psycopg": "psycopg",
    "asyncpg": "asyncpg",
    "argon2-cffi": "argon2",
    "beautifulsoup4": "bs4",
    "pillow": "PIL",
    "pyyaml": "yaml",
    "python-dotenv": "dotenv",
    "python-multipart": "multipart",
    "mysqlclient": "MySQLdb",
    "pymysql": "pymysql",
    "scikit-learn": "sklearn",
    "opencv-python": "cv2",
    "python-dateutil": "dateutil",
}


def _versionless(spec: str) -> str:
    spec = spec.strip().strip("'\"")
    spec = re.split(r"\s*(?:[<>=!~;\[@]|\s+-\s+)", spec, maxsplit=1)[0]
    return spec.strip()


def _collect_python_manifests(root: Path) -> tuple[set[str], list[str]]:
    declared: set[str] = set()
    files: list[str] = []

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        files.append("pyproject.toml")
        data = load_toml(pyproject)
        project = data.get("project") or {}
        for spec in project.get("dependencies") or []:
            declared.add(normalize_package(_versionless(str(spec))))
        for group in (project.get("optional-dependencies") or {}).values():
            for spec in group or []:
                declared.add(normalize_package(_versionless(str(spec))))
        build = data.get("build-system") or {}
        for spec in build.get("requires") or []:
            declared.add(normalize_package(_versionless(str(spec))))
        poetry = (data.get("tool") or {}).get("poetry") or {}
        for name in (poetry.get("dependencies") or {}):
            declared.add(normalize_package(name))
        for group in (poetry.get("group") or {}).values():
            for name in (group.get("dependencies") or {}):
                declared.add(normalize_package(name))
        groups = data.get("dependency-groups") or {}
        for group in groups.values():
            for spec in group or []:
                if isinstance(spec, str):
                    declared.add(normalize_package(_versionless(spec)))

    for req in sorted(root.glob("requirements*.txt")):
        files.append(relpath(req, root))
        for line in read_text(req).splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "-", "git+", "http")):
                continue
            name = _versionless(line)
            if name:
                declared.add(normalize_package(name))

    pipfile = root / "Pipfile"
    if pipfile.is_file():
        files.append("Pipfile")
        data = load_toml(pipfile)
        for section in ("packages", "dev-packages"):
            for name in (data.get(section) or {}):
                declared.add(normalize_package(name))

    return declared, files


def _collect_node_manifests(root: Path) -> tuple[set[str], list[str]]:
    declared: set[str] = set()
    files: list[str] = []
    manifest = root / "package.json"
    if manifest.is_file():
        files.append("package.json")
        data = load_json(manifest) or {}
        for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            for name in (data.get(section) or {}):
                declared.add(normalize_package(name))
    return declared, files


def _collect_other_manifests(root: Path) -> tuple[set[str], list[str]]:
    declared: set[str] = set()
    files: list[str] = []
    pom = root / "pom.xml"
    if pom.is_file():
        files.append("pom.xml")
        text = read_text(pom).lower()
        for match in re.finditer(r"<artifactId>\s*([\w.\-]+)\s*</artifactId>", text):
            declared.add(normalize_package(match.group(1)))
    for gradle_name in ("build.gradle", "build.gradle.kts"):
        gradle = root / gradle_name
        if gradle.is_file():
            files.append(gradle_name)
            text = read_text(gradle)
            for match in re.finditer(
                r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation)\s*\(?\s*[\"']([^:\"']+):([^:\"']+)",
                text,
            ):
                declared.add(normalize_package(match.group(2)))
    gomod = root / "go.mod"
    if gomod.is_file():
        files.append("go.mod")
        for match in re.finditer(r"^\s*(?:require\s+)?([\w.\-/]+)\s+v[\w.\-+]+", read_text(gomod), re.M):
            declared.add(normalize_package(match.group(1)))
    cargo = root / "Cargo.toml"
    if cargo.is_file():
        files.append("Cargo.toml")
        data = load_toml(cargo)
        for section in ("dependencies", "dev-dependencies", "build-dependencies"):
            for name in (data.get(section) or {}):
                declared.add(normalize_package(name))
    composer = root / "composer.json"
    if composer.is_file():
        files.append("composer.json")
        data = load_json(composer) or {}
        for section in ("require", "require-dev"):
            for name in (data.get(section) or {}):
                declared.add(normalize_package(name))
    gemfile = root / "Gemfile"
    if gemfile.is_file():
        files.append("Gemfile")
        for match in re.finditer(r"^\s*gem\s+[\"']([^\"']+)", read_text(gemfile), re.M):
            declared.add(normalize_package(match.group(1)))
    return declared, files


def _installed(candidate: str, module: str | None) -> bool:
    names = [module] if module else []
    names.append(IMPORT_NAMES.get(normalize_package(candidate), candidate.replace("-", "_")))
    for name in names:
        if not name:
            continue
        try:
            if importlib.util.find_spec(name) is not None:
                return True
        except (ImportError, ValueError, ModuleNotFoundError, AttributeError):
            continue
    return False


class DependencyVerifier:
    name = "dependency"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        candidates = [str(c) for c in (claim.args.get("candidates") or [])]
        if not candidates:
            name = str(claim.args.get("name") or "")
            candidates = [name] if name else []
        if not candidates:
            return fail("No dependency name was specified")
        module = claim.args.get("module")
        declared_only = bool(claim.args.get("declared_only"))

        py_declared, py_files = _collect_python_manifests(ctx.root)
        node_declared, node_files = _collect_node_manifests(ctx.root)
        other_declared, other_files = _collect_other_manifests(ctx.root)
        declared = py_declared | node_declared | other_declared
        manifests = py_files + node_files + other_files

        matched_declared: list[str] = []
        for candidate in candidates:
            norm = normalize_package(candidate)
            if norm in declared:
                matched_declared.append(candidate)
                continue
            # Java / Gradle artifact matching is intentionally permissive:
            # spring-boot-starter-web satisfies "spring-boot".
            for dep in declared:
                if dep.startswith(norm + "-") or dep.endswith("-" + norm) or norm in dep.split("-"):
                    matched_declared.append(candidate)
                    break

        installed_matches = [c for c in candidates if _installed(c, module)]
        local_module = bool(module) and _local_module_exists(ctx.root, str(module))

        payload = {
            "candidates": candidates,
            "declared": sorted(declared)[:200],
            "manifests": manifests,
            "installed": installed_matches,
            "module": module,
        }

        if matched_declared:
            return ok(
                f"Dependency `{candidates[0]}` is declared by the project",
                details=f"Declared in {', '.join(manifests) or 'project manifest'}",
                payload=payload,
                deps=manifests,
            )
        if declared_only:
            return fail(
                f"Dependency `{candidates[0]}` is not declared by the project",
                details=f"Checked manifests: {', '.join(manifests) or 'none found'}",
                payload=payload,
                deps=manifests,
            )
        if local_module:
            return ok(
                f"`{module}` resolves to project code",
                details="Found a local module with this name.",
                payload=payload,
                deps=[],
            )
        if installed_matches:
            return warn(
                f"Dependency `{installed_matches[0]}` is installed but not declared",
                details="The environment has it, but a clean install may not.",
                payload=payload,
                deps=manifests,
                confidence=0.6,
            )
        return fail(
            f"Dependency `{candidates[0]}` is neither declared nor installed",
            details=(
                f"Checked manifests: {', '.join(manifests) or 'none found'}. "
                f"Checked imports: {module or ', '.join(candidates)}."
            ),
            payload=payload,
            deps=manifests,
            confidence=0.9,
        )


def _local_module_exists(root: Path, module: str) -> bool:
    top = module.split(".")[0]
    return any(
        p.is_file()
        for p in (
            root / f"{top}.py",
            root / top / "__init__.py",
            root / "src" / f"{top}.py",
            root / "src" / top / "__init__.py",
        )
    )


class LockfileConsistencyVerifier:
    name = "lockfile_consistency"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        manifest = str(claim.args.get("manifest") or "package.json")
        lockfile = str(claim.args.get("lockfile") or "")
        manifest_path = ctx.root / manifest
        if not manifest_path.is_file():
            return fail(f"Dependency manifest `{manifest}` does not exist", payload={"manifest": manifest})

        lock_candidates = (
            [lockfile] if lockfile else
            ["package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "Cargo.lock"]
        )
        found = [name for name in lock_candidates if (ctx.root / name).is_file()]
        if not found:
            return fail(
                "No supported dependency lockfile was found",
                details="Checked: " + ", ".join(lock_candidates),
                payload={"manifest": manifest, "checked": lock_candidates},
                deps=[manifest],
            )

        try:
            if manifest_path.suffix.lower() == ".json":
                manifest_data = load_json(manifest_path) or {}
                direct = set()
                if isinstance(manifest_data, dict):
                    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                        direct.update(str(name) for name in (manifest_data.get(section) or {}))
                lock_text = read_text(ctx.root / found[0])
                missing = sorted(name for name in direct if name not in lock_text)
            else:
                direct = set()
                missing = []
                lock_text = read_text(ctx.root / found[0])
        except Exception as exc:
            return fail(
                f"Dependency lockfile verification failed: {type(exc).__name__}",
                details=str(exc),
                payload={"manifest": manifest, "lockfile": found[0]},
                deps=[manifest, found[0]],
            )

        deps = [manifest, found[0]]
        if missing:
            return fail(
                f"Lockfile `{found[0]}` may be missing {len(missing)} manifest dependency/dependencies",
                details=", ".join(missing[:20]),
                payload={"manifest": manifest, "lockfile": found[0], "missing": missing[:100]},
                deps=deps,
                confidence=0.75,
            )
        return ok(
            f"Lockfile `{found[0]}` is present and covers the manifest",
            details=f"Checked {len(direct)} direct dependency name(s).",
            payload={"manifest": manifest, "lockfile": found[0], "direct_dependencies": sorted(direct)},
            deps=deps,
            confidence=0.85,
        )
