"""Environment verifier: versions, tools, ports and environment variables."""

from __future__ import annotations

import json
import os
import re
import socket
import sys
from pathlib import Path

from ..models import Claim, EvidenceResult
from ..util import load_json, load_toml, read_text, redact_secret, run_command, which
from .base import VerifyContext, fail, ok, warn

_NUM = re.compile(r"(\d+(?:\.\d+)*)")


def _first_version(text: str) -> str | None:
    match = _NUM.search(text or "")
    return match.group(1) if match else None


def _manifest_versions(root: Path, technology: str) -> tuple[str | None, list[str], str]:
    """Return (version, files_used, source_note)."""
    tech = technology.lower().replace(".js", "").strip()
    files: list[str] = []
    candidates: list[tuple[str, str]] = []

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        files.append("pyproject.toml")
        data = load_toml(pyproject)
        project = data.get("project") or {}
        if tech == "python":
            requires = str(project.get("requires-python") or "")
            m = _NUM.search(requires)
            if m:
                candidates.append((m.group(1), "pyproject.toml requires-python"))
        poetry = (data.get("tool") or {}).get("poetry") or {}
        deps = poetry.get("dependencies") or {}
        if tech in deps and isinstance(deps[tech], str):
            candidates.append((_first_version(deps[tech]) or "", f"pyproject.toml poetry.{tech}"))

    pkg = root / "package.json"
    if pkg.is_file():
        files.append("package.json")
        data = load_json(pkg) or {}
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        if tech in deps:
            candidates.append((_first_version(str(deps[tech])) or "", f"package.json {tech}"))
        if tech == "node":
            engines = (data.get("engines") or {}).get("node")
            if engines:
                candidates.append((_first_version(str(engines)) or "", "package.json engines.node"))

    pom = root / "pom.xml"
    if pom.is_file():
        files.append("pom.xml")
        text = read_text(pom)
        if tech == "java":
            for pattern in (
                r"<java\.version>\s*([\w.\-]+)\s*</java\.version>",
                r"<maven\.compiler\.(?:release|source|target)>\s*([\w.\-]+)\s*</maven\.compiler\.(?:release|source|target)>",
            ):
                m = re.search(pattern, text)
                if m:
                    candidates.append((_first_version(m.group(1)) or "", "pom.xml compiler config"))
        if tech in {"spring", "spring boot", "springboot"}:
            m = re.search(r"<artifactId>\s*spring-boot-starter-parent\s*</artifactId>\s*<version>\s*([\w.\-]+)", text)
            if m:
                candidates.append((_first_version(m.group(1)) or "", "pom.xml Spring Boot parent"))
            m = re.search(r"<artifactId>\s*spring-boot[^<]*</artifactId>\s*<version>\s*([\w.\-]+)", text)
            if m:
                candidates.append((_first_version(m.group(1)) or "", "pom.xml Spring Boot dependency"))

    for gradle_name in ("build.gradle", "build.gradle.kts"):
        gradle = root / gradle_name
        if gradle.is_file():
            files.append(gradle_name)
            text = read_text(gradle)
            if tech == "java":
                m = re.search(r"(?:sourceCompatibility|targetCompatibility|languageVersion)\s*[=:(]\s*[\"']?([\w.]+)", text)
                if m:
                    candidates.append((_first_version(m.group(1)) or "", f"{gradle_name} compiler config"))
            if tech in {"spring", "spring boot", "springboot"}:
                m = re.search(r"org\.springframework\.boot[^\n]*version\s+[\"']([\w.\-]+)", text)
                if m:
                    candidates.append((_first_version(m.group(1)) or "", f"{gradle_name} Spring Boot plugin"))

    gomod = root / "go.mod"
    if gomod.is_file():
        files.append("go.mod")
        if tech == "go":
            m = re.search(r"^go\s+([\d.]+)", read_text(gomod), re.M)
            if m:
                candidates.append((m.group(1), "go.mod go directive"))

    cargo = root / "Cargo.toml"
    if cargo.is_file():
        files.append("Cargo.toml")
        if tech == "rust":
            data = load_toml(cargo)
            edition = (data.get("package") or {}).get("edition")
            if edition:
                candidates.append((str(edition), "Cargo.toml edition"))

    nvmrc = root / ".nvmrc"
    if nvmrc.is_file() and tech == "node":
        files.append(".nvmrc")
        candidates.append((_first_version(read_text(nvmrc)) or "", ".nvmrc"))

    python_version_file = root / ".python-version"
    if python_version_file.is_file() and tech == "python":
        files.append(".python-version")
        candidates.append((_first_version(read_text(python_version_file)) or "", ".python-version"))

    for name in ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"):
        compose = root / name
        if compose.is_file() and tech in {"postgresql", "postgres", "mysql", "redis", "mongo", "mongodb"}:
            files.append(name)
            text = read_text(compose)
            m = re.search(rf"image:\s*[\"']?{tech}[:@]([\w.\-]+)", text, re.I)
            if m:
                candidates.append((m.group(1), f"{name} image tag"))

    for version, note in candidates:
        if version:
            return version, files, note
    return None, files, ""


def _runtime_version(technology: str) -> tuple[str | None, str]:
    tech = technology.lower().replace(".js", "").strip()
    if tech == "python":
        return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", "running interpreter"
    tool_map = {
        "java": ("java", ["-version"]),
        "node": ("node", ["--version"]),
        "go": ("go", ["version"]),
        "rust": ("rustc", ["--version"]),
        "docker": ("docker", ["--version"]),
        "postgresql": ("psql", ["--version"]),
        "postgres": ("psql", ["--version"]),
        "mysql": ("mysql", ["--version"]),
        "redis": ("redis-server", ["--version"]),
    }
    if tech not in tool_map:
        return None, ""
    tool, args = tool_map[tech]
    if not which(tool):
        return None, f"{tool} not installed"
    result = run_command([tool, *args], Path.cwd(), timeout=10)
    text = result.stdout or result.stderr
    return _first_version(text), f"`{tool} {' '.join(args)}`"


def _version_matches(claimed: str, actual: str) -> bool:
    claimed = claimed.strip().lstrip("v")
    actual = actual.strip().lstrip("v")
    if claimed == actual:
        return True
    if actual.startswith(claimed) or claimed.startswith(actual):
        return True
    c = claimed.split(".")
    a = actual.split(".")
    return len(c) >= 2 and len(a) >= 2 and c[0] == a[0] and c[1] == a[1]


class EnvironmentVerifier:
    name = "environment"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        args = claim.args
        if "port" in args:
            return self._port(int(args["port"]), ctx)
        if "variable" in args:
            return self._env_var(str(args["variable"]))
        if "tool" in args:
            tool = str(args["tool"])
            found = which(tool)
            if found:
                return ok(f"Tool `{tool}` is available", details=found, payload={"tool": tool, "path": found})
            return fail(f"Tool `{tool}` is not installed", payload={"tool": tool})
        if args.get("running"):
            return warn(
                "Generic runtime state cannot be proven without a concrete probe",
                details=(
                    "VERITY needs a port, URL, process name or command to verify that "
                    "something is running. The claim remains unverified."
                ),
                confidence=0.3,
            )

        technology = str(args.get("technology") or "")
        version = str(args.get("version") or "") or None
        if not technology:
            return warn("No environment fact was specified for this claim")

        manifest_version, files, note = _manifest_versions(ctx.root, technology)
        runtime_version, runtime_note = _runtime_version(technology)
        actual = manifest_version or runtime_version
        source = note or runtime_note
        payload = {
            "technology": technology,
            "claimed_version": version,
            "manifest_version": manifest_version,
            "runtime_version": runtime_version,
            "source": source,
            "files": files,
        }

        if not actual:
            return warn(
                f"Could not determine the {technology} version",
                details=(
                    f"No manifest or runtime probe produced a version. Checked files: "
                    f"{', '.join(files) or 'none'}."
                ),
                payload=payload,
                deps=files,
                confidence=0.4,
            )
        if version and not _version_matches(version, actual):
            return fail(
                f"Claimed {technology} {version}, but the project uses {actual}",
                details=f"Evidence source: {source}",
                payload=payload,
                deps=files,
                confidence=0.9,
            )
        suffix = f" {version}" if version else ""
        return ok(
            f"{technology}{suffix} matches the project environment",
            details=f"Detected {actual} ({source})",
            payload=payload,
            deps=files,
        )

    def _port(self, port: int, ctx: VerifyContext) -> EvidenceResult:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return ok(
                    f"Something is listening on port {port}",
                    details="TCP connection to 127.0.0.1 succeeded.",
                    payload={"port": port},
                )
        except OSError as exc:
            return fail(
                f"Nothing is listening on port {port}",
                details=str(exc),
                payload={"port": port},
            )

    def _env_var(self, name: str) -> EvidenceResult:
        value = os.environ.get(name)
        if value is None:
            return fail(f"Environment variable `{name}` is not set", payload={"variable": name})
        return ok(
            f"Environment variable `{name}` is set",
            details=f"Value: {redact_secret(value)}",
            payload={"variable": name, "redacted": redact_secret(value)},
        )
