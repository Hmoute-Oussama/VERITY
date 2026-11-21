"""Test-execution verifier.

Executing commands is the most powerful evidence source, and therefore the
most dangerous one. VERITY refuses to run arbitrary agent-supplied commands
unless the project explicitly opts in.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..models import Claim, EvidenceResult
from ..util import code_files_for_digest, load_json, load_toml, read_text, relpath, run_command
from .base import VerifyContext, fail, ok, warn

DANGEROUS_PATTERNS = [
    r"\brm\s+-[a-z]*r[a-z]*f",
    r"\bdel\s+/[fsq]",
    r"\bformat\s+[a-z]:",
    r"\bmkfs(?:\.\w+)?\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bsudo\b",
    r"\b(?:curl|wget)\b[^|]*\|\s*(?:ba|z|k)?sh\b",
    r">\s*/dev/sd",
    r":\(\)\s*\{.*\};:",
    r"\bchmod\s+777\s+/",
    r"\bgit\s+push\s+--force\b",
]

_PYTEST_SUMMARY = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")
_PYTEST_TOTAL = re.compile(r"(?:=+\s*)?(\d+)\s+(passed|failed|error|errors)")


def is_dangerous(command: str) -> str | None:
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.I):
            return pattern
    return None


def is_allowlisted(command: str, ctx: VerifyContext) -> bool:
    if ctx.config.verification.allow_exec:
        return True
    command = command.strip()
    for allowed in ctx.config.verification.allowed_commands:
        if command == allowed or command.startswith(allowed + " "):
            return True
    return False


def detect_test_command(root: Path) -> tuple[str | None, str]:
    """Return (command, reason) for the project's most likely test runner."""
    if (root / "pytest.ini").is_file() or (root / "conftest.py").is_file() or (root / "tests").is_dir():
        return "pytest -q", "pytest project layout detected"
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        data = load_toml(pyproject)
        if (data.get("tool") or {}).get("pytest") is not None:
            return "pytest -q", "pyproject.toml [tool.pytest] detected"
        deps = " ".join(
            str(x) for x in ((data.get("project") or {}).get("dependencies") or [])
        ).lower()
        if "pytest" in deps:
            return "pytest -q", "pytest is a declared dependency"
    package = root / "package.json"
    if package.is_file():
        data = load_json(package) or {}
        if (data.get("scripts") or {}).get("test"):
            return "npm test", "package.json defines a test script"
    if (root / "pom.xml").is_file():
        if (root / "mvnw").is_file():
            return "./mvnw -q test", "Maven wrapper detected"
        return "mvn -q test", "pom.xml detected"
    if (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        if (root / "gradlew").is_file():
            return "./gradlew test", "Gradle wrapper detected"
        return "gradle test", "Gradle build detected"
    if (root / "go.mod").is_file():
        return "go test ./...", "go.mod detected"
    if (root / "Cargo.toml").is_file():
        return "cargo test", "Cargo.toml detected"
    if any(root.glob("*.sln")) or any(root.glob("*.csproj")):
        return "dotnet test", ".NET project detected"
    return None, "no supported test runner detected"


class TestExecutionVerifier:
    name = "test_execution"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        if os.environ.get("VERITY_DISABLE_EXEC") == "1":
            return warn(
                "Test execution disabled by VERITY_DISABLE_EXEC",
                details="Set VERITY_DISABLE_EXEC=0 to allow test execution.",
            )

        configured = str(claim.args.get("command") or "").strip()
        if not configured:
            default = str(ctx.config.verification.test_command or "auto")
            if default and default != "auto":
                configured = default
        reason = "explicit claim command"
        if not configured:
            configured, reason = detect_test_command(ctx.root)
        if not configured:
            return warn(
                "No test command could be determined",
                details=reason,
                payload={"reason": reason},
            )

        dangerous = is_dangerous(configured)
        if dangerous:
            return fail(
                "Refusing to execute a dangerous command",
                details=f"Command `{configured}` matched safety rule `{dangerous}`.",
                payload={"command": configured},
                confidence=1.0,
            )
        if not is_allowlisted(configured, ctx):
            return warn(
                "Test command is not allowlisted — not executed",
                details=(
                    f"`{configured}` is not in verification.allowed_commands. "
                    "Add it to .verity/config.yaml or set verification.allow_exec: true."
                ),
                payload={"command": configured, "reason": reason},
                confidence=0.4,
            )

        timeout = float(ctx.config.verification.timeout_seconds or 300)
        result = run_command(
            configured,
            ctx.root,
            timeout=timeout,
            sandbox_mode=ctx.config.verification.sandbox_mode,
            sandbox_image=ctx.config.verification.sandbox_image,
            sandbox_allow_network=ctx.config.verification.sandbox_allow_network,
        )
        output = result.tail(ctx.config.verification.max_output_chars)
        summary_counts = {m.group(2): int(m.group(1)) for m in _PYTEST_SUMMARY.finditer(output)}
        payload = {
            "command": configured,
            "reason": reason,
            "returncode": result.returncode,
            "duration_seconds": result.duration_seconds,
            "timed_out": result.timed_out,
            "summary": summary_counts,
        }
        deps = code_files_for_digest(ctx.root) if ctx.config.evidence.track_all_code else []

        if result.timed_out:
            return fail(
                f"Test command timed out after {timeout:g}s",
                details=output,
                payload=payload,
                deps=deps,
                confidence=0.8,
            )
        if result.returncode == 0:
            counts = ", ".join(f"{v} {k}" for k, v in summary_counts.items()) or "exit code 0"
            return ok(
                f"Test command passed ({counts})",
                details=f"$ {configured}\n{output}",
                payload=payload,
                deps=deps,
            )
        return fail(
            f"Test command failed with exit code {result.returncode}",
            details=f"$ {configured}\n{output}",
            payload=payload,
            deps=deps,
            confidence=1.0,
        )
