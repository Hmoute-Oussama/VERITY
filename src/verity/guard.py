"""The Hallucination Firewall.

VERITY does not block uncertainty. It blocks *actions whose prerequisites are
demonstrably absent* — and it shows the agent the exact evidence.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .engine import Engine
from .models import Decision, DependencyCheck, Risk, Verdict
from .util import CODE_EXTS, glob_paths, load_json, load_yaml, read_text, relpath, run_command, which

STDLIB = set(getattr(sys, "stdlib_module_names", set()))
_PATH_TOKEN = re.compile(
    r"(?<![\w./-])([\w./-]+\.(?:py|pyi|js|jsx|ts|tsx|mjs|cjs|java|kt|go|rs|ya?ml|json|toml|ini|cfg|sql|sh|ps1|env))(?![\w./-])",
    re.I,
)
_SCRIPT = re.compile(r"\b(?:npm|pnpm|yarn)\s+run\s+([\w:.-]+)")
_INSTALL = re.compile(r"\b(?:pip3?|npm|pnpm|yarn|poetry|uv)\s+(?:install|add|i)\b")
_SEGMENT_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")


@dataclass
class Assumption:
    kind: str
    description: str
    severity: Risk
    evidence: str
    hint: str = ""
    path: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "description": self.description,
            "severity": self.severity.value,
            "evidence": self.evidence,
            "hint": self.hint,
            "path": self.path,
        }


def _check(
    kind: str,
    description: str,
    satisfied: bool,
    risk: Risk,
    evidence: str = "",
    hint: str = "",
) -> DependencyCheck:
    return DependencyCheck(
        kind=kind,
        description=description,
        satisfied=satisfied,
        risk=risk,
        evidence=evidence,
        hint=hint,
    )


def _file_check(root: Path, candidates: list[str], description: str, risk: Risk, hint: str) -> DependencyCheck:
    found = [relpath(p, root) for c in candidates for p in glob_paths(root, c) if p.is_file()]
    if found:
        return _check("missing_file", description, True, risk, evidence=", ".join(found[:5]), hint=hint)
    return _check(
        "missing_file",
        description,
        False,
        risk,
        evidence=f"None of these paths exist: {', '.join(candidates)}",
        hint=hint,
    )


def _unresolved_imports(root: Path, rel_path: str) -> list[str]:
    path = root / rel_path
    if not path.is_file():
        return []
    try:
        tree = ast.parse(read_text(path))
    except SyntaxError:
        return []
    missing: list[str] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            modules = [(node.module or "").split(".")[0]]
        for module in modules:
            if not module or module in STDLIB:
                continue
            if (root / f"{module}.py").is_file() or (root / module / "__init__.py").is_file():
                continue
            if (root / "src" / f"{module}.py").is_file() or (root / "src" / module / "__init__.py").is_file():
                continue
            try:
                if importlib.util.find_spec(module) is not None:
                    continue
            except (ImportError, ValueError, ModuleNotFoundError, AttributeError):
                pass
            missing.append(module)
    return sorted(set(missing))


def _check_python_script(root: Path, script: str) -> list[DependencyCheck]:
    checks: list[DependencyCheck] = []
    path = (root / script).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return [_check("path_escape", f"`{script}` is outside the project root", False, Risk.HIGH,
                       evidence=str(path), hint="Run only project-local scripts.")]
    if not path.is_file():
        checks.append(
            _check(
                "missing_file",
                f"Script `{script}` exists",
                False,
                Risk.HIGH,
                evidence=f"{script} not found in the project",
                hint="Create the script or correct the path before running it.",
            )
        )
        return checks
    checks.append(_check("missing_file", f"Script `{script}` exists", True, Risk.MEDIUM, evidence=relpath(path, root)))
    missing = _unresolved_imports(root, relpath(path, root))
    if missing:
        checks.append(
            _check(
                "missing_dependency",
                "All imports of the script resolve",
                False,
                Risk.HIGH,
                evidence="Unresolved imports: " + ", ".join(missing),
                hint="Install the missing packages or correct the imports.",
            )
        )
    else:
        checks.append(
            _check("missing_dependency", "All imports of the script resolve", True, Risk.MEDIUM,
                   evidence=f"Scanned {relpath(path, root)}")
        )
    return checks


def _check_package_script(root: Path, script: str) -> list[DependencyCheck]:
    manifest = root / "package.json"
    if not manifest.is_file():
        return [
            _check(
                "missing_file",
                "package.json exists",
                False,
                Risk.HIGH,
                evidence="No package.json in the project root",
                hint="Create package.json before running npm scripts.",
            )
        ]
    data = load_json(manifest) or {}
    scripts = data.get("scripts") or {}
    if script in scripts:
        return [
            _check("missing_file", "package.json exists", True, Risk.LOW, evidence="package.json"),
            _check(
                "missing_script",
                f"npm script `{script}` is defined",
                True,
                Risk.MEDIUM,
                evidence=f"scripts.{script} = {str(scripts[script])[:120]}",
            ),
        ]
    available = ", ".join(sorted(scripts)) or "none"
    return [
        _check("missing_file", "package.json exists", True, Risk.LOW, evidence="package.json"),
        _check(
            "missing_script",
            f"npm script `{script}` is defined",
            False,
            Risk.HIGH,
            evidence=f"Available scripts: {available}",
            hint=f"Use an existing script or add `{script}` to package.json.",
        ),
    ]


def _check_compose(root: Path) -> list[DependencyCheck]:
    candidates = ["compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"]
    check = _file_check(
        root,
        candidates,
        "Docker Compose configuration exists",
        Risk.HIGH,
        "Create compose.yaml (or docker-compose.yml) before running docker compose.",
    )
    if not check.satisfied:
        return [check]
    path = next((root / c for c in candidates if (root / c).is_file()), None)
    if path is None:
        return [check]
    data = load_yaml(path)
    services = (data or {}).get("services") if isinstance(data, dict) else None
    if not services:
        return [
            check,
            _check(
                "invalid_config",
                "Compose file declares at least one service",
                False,
                Risk.HIGH,
                evidence=f"{relpath(path, root)} has no non-empty `services` mapping",
                hint="Add a services section to the compose file.",
            ),
        ]
    return [check, _check("invalid_config", "Compose file declares services", True, Risk.LOW,
                          evidence=", ".join(sorted(services)[:8]))]


def _check_git_push(engine: Engine) -> list[DependencyCheck]:
    if not engine.config.policy.require_verification_before_push:
        return []
    status = engine.status()
    ok = status.overall == "verified"
    return [
        _check(
            "unverified",
            "Project verification is complete before push",
            ok,
            Risk.HIGH,
            evidence=f"Overall status: {status.overall} "
            f"({status.counts.get('contradicted', 0)} contradicted, "
            f"{status.counts.get('stale', 0)} stale)",
            hint="Run `verity verify` and resolve contradicted/stale claims.",
        )
    ]


def _rule_checks(engine: Engine, segment: str) -> list[DependencyCheck]:
    root = engine.root
    lowered = segment.lower().strip()
    checks: list[DependencyCheck] = []
    matched = False

    if re.search(r"\bdocker(?:-|\s+)compose\s+(?:up|build|run|start|restart)\b", lowered):
        matched = True
        checks.extend(_check_compose(root))

    if re.search(r"\bdocker\s+build\b", lowered):
        matched = True
        checks.append(
            _file_check(root, ["Dockerfile", "dockerfile", "Containerfile"], "Dockerfile exists",
                        Risk.HIGH, "Create a Dockerfile before building the image.")
        )

    script_match = _SCRIPT.search(segment)
    if script_match:
        matched = True
        checks.extend(_check_package_script(root, script_match.group(1)))

    if re.search(r"\b(?:mvnw?|gradle|\./gradlew)\s+", lowered):
        matched = True
        checks.append(
            _file_check(
                root,
                ["pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"],
                "A Maven or Gradle build descriptor exists",
                Risk.HIGH,
                "Initialize the build before running build commands.",
            )
        )

    if re.search(r"\bgo\s+(?:test|build|run)\b", lowered):
        matched = True
        checks.append(
            _file_check(root, ["go.mod"], "go.mod exists", Risk.HIGH, "Run `go mod init` first.")
        )

    if re.search(r"\bcargo\s+(?:test|build|run)\b", lowered):
        matched = True
        checks.append(
            _file_check(root, ["Cargo.toml"], "Cargo.toml exists", Risk.HIGH, "Run `cargo init` first.")
        )

    if re.search(r"\bdotnet\s+(?:test|build|run)\b", lowered):
        matched = True
        checks.append(
            _check(
                "missing_file",
                "A .NET project file exists",
                bool(list(root.glob("*.csproj")) or list(root.glob("*.sln"))),
                Risk.HIGH,
                evidence="No *.csproj or *.sln in the project root",
                hint="Create or restore the .NET project first.",
            )
        )

    if re.search(r"\balembic\s+upgrade\b", lowered):
        matched = True
        checks.append(
            _file_check(root, ["alembic.ini"], "alembic.ini exists", Risk.HIGH,
                        "Initialize Alembic before running migrations.")
        )
        checks.append(
            _check(
                "missing_file",
                "A migrations directory exists",
                any((root / p).is_dir() for p in ("migrations", "alembic", "db/migrations")),
                Risk.HIGH,
                evidence="No migrations/, alembic/ or db/migrations/ directory",
                hint="Run `alembic init` to create the migration environment.",
            )
        )

    if re.search(r"\bpytest\b|\bpython3?\s+-m\s+pytest\b", lowered):
        matched = True
        # Any bare positional argument to pytest is a path or node id. Options
        # that take a value (``-k expr``, ``-m marker``, ...) are skipped so we
        # do not mistake their values for test paths.
        value_options = {
            "-k", "-m", "-p", "-o", "-W", "-n", "-c", "--maxfail", "--tb",
            "--cov", "--cov-report", "--rootdir", "--junitxml", "--durations",
            "--ignore", "--ignore-glob", "--deselect", "--basetemp",
            "--confcutdir", "--import-mode", "--log-level", "--log-file",
            "--capture", "--color", "--override-ini", "--dist", "--timeout",
        }
        skip_next = False
        for token in segment.split()[1:]:
            if skip_next:
                skip_next = False
                continue
            if token.startswith("-"):
                skip_next = token in value_options
                continue
            if token in {"pytest", "python", "python3", "-m"}:
                continue
            candidate = root / token.split("::")[0]
            checks.append(
                _check(
                    "missing_file",
                    f"Test path `{token}` exists",
                    candidate.exists(),
                    Risk.MEDIUM,
                    evidence=str(candidate),
                    hint="Correct the test path or create the test file.",
                )
            )
            break

    py_match = re.search(r"\b(?:python3?|py)\s+(?!-m\b)([^\s]+\.py)\b", segment, re.I)
    if py_match:
        matched = True
        checks.extend(_check_python_script(root, py_match.group(1)))

    node_match = re.search(r"\bnode\s+([^\s]+\.(?:js|mjs|cjs))\b", segment, re.I)
    if node_match:
        matched = True
        checks.append(
            _file_check(root, [node_match.group(1)], f"Script `{node_match.group(1)}` exists",
                        Risk.HIGH, "Correct the script path.")
        )

    if re.search(r"\bmake\s+([\w.-]+)", segment):
        match = re.search(r"\bmake\s+([\w.-]+)", segment)
        matched = True
        makefile = next((root / n for n in ("Makefile", "makefile", "GNUmakefile") if (root / n).is_file()), None)
        if makefile is None:
            checks.append(
                _check("missing_file", "A Makefile exists", False, Risk.HIGH,
                       evidence="No Makefile in the project root", hint="Create a Makefile first.")
            )
        else:
            target = match.group(1) if match else ""
            defined = bool(re.search(rf"^{re.escape(target)}\s*:", read_text(makefile), re.M))
            checks.append(
                _check("missing_script", f"Make target `{target}` is defined", defined, Risk.HIGH,
                       evidence=f"{relpath(makefile, root)}", hint="Add the target to the Makefile.")
            )

    if re.search(r"\bgit\s+push\b", lowered):
        matched = True
        checks.extend(_check_git_push(engine))

    if _INSTALL.search(segment):
        matched = True

    if re.search(r"\b(?:psql|mysql|redis-cli|mongo|mongosh)\b", lowered):
        matched = True
        for tool in ("psql", "mysql", "redis-cli", "mongosh"):
            if tool in lowered:
                checks.append(
                    _check("missing_tool", f"`{tool}` is installed", which(tool) is not None, Risk.MEDIUM,
                           evidence=which(tool) or f"{tool} is not on PATH",
                           hint=f"Install {tool} or use a containerized client.")
                )
                break

    if not matched:
        # Conservative generic check: a referenced local path with an extension
        # that does not exist is worth a warning, never a hard block.
        for match in _PATH_TOKEN.finditer(segment):
            token = match.group(1)
            if token.startswith(("http", "/dev/", "/tmp/")):
                continue
            candidate = root / token
            if candidate.exists():
                continue
            if any(part in token for part in ("*", "${", "{{", "<")):
                continue
            checks.append(
                _check(
                    "missing_file",
                    f"Referenced path `{token}` exists",
                    False,
                    Risk.MEDIUM,
                    evidence=f"{token} does not exist in the project",
                    hint="Confirm the path or create the file before relying on it.",
                )
            )
            break
    return checks


def check_action(engine: Engine, action: str, *, strict: bool | None = None) -> Decision:
    action = action.strip()
    if not action:
        return Decision(action=action, verdict=Verdict.ALLOW, risk=Risk.LOW, reasons=["Empty command"])

    policy = engine.config.policy
    strict = policy.strict if strict is None else strict

    for denied in policy.deny_commands:
        if re.search(denied, action, re.I):
            return Decision(
                action=action,
                verdict=Verdict.BLOCK,
                risk=Risk.CRITICAL,
                reasons=[f"Command matches policy deny rule `{denied}`"],
            )
    for allowed in policy.allow_commands:
        if re.search(allowed, action, re.I):
            return Decision(
                action=action,
                verdict=Verdict.ALLOW,
                risk=Risk.LOW,
                reasons=[f"Command matches policy allow rule `{allowed}`"],
            )

    checks: list[DependencyCheck] = []
    for segment in _SEGMENT_SPLIT.split(action):
        if segment.strip():
            checks.extend(_rule_checks(engine, segment))

    missing = [c for c in checks if not c.satisfied]
    risk = max((c.risk for c in checks), key=lambda r: r.rank, default=Risk.LOW)
    block_rank = Risk(policy.block_risk).rank
    warn_rank = Risk(policy.warn_risk).rank

    if strict and missing:
        verdict = Verdict.BLOCK
    elif any(c.risk.rank >= block_rank for c in missing):
        verdict = Verdict.BLOCK
    elif any(c.risk.rank >= warn_rank for c in missing):
        verdict = Verdict.WARN
    else:
        verdict = Verdict.ALLOW

    reasons: list[str] = []
    for check in missing:
        reasons.append(f"{check.description} — {check.evidence}")
    if not checks:
        reasons.append("No prerequisite assumptions were detected for this action.")
    elif not missing:
        reasons.append("All detected prerequisites are satisfied.")

    return Decision(action=action, verdict=verdict, risk=risk, checks=checks, reasons=reasons)


def execute_guarded(engine: Engine, action: str, *, strict: bool | None = None) -> int:
    decision = check_action(engine, action, strict=strict)
    if decision.verdict == Verdict.BLOCK:
        return 2
    engine.repo.record_event("action_executed", decision.to_dict())
    try:
        argv = shlex.split(action, posix=os.name != "nt")
        if not argv:
            return 2
        proc = run_command(
            argv,
            engine.root,
            timeout=engine.config.verification.timeout_seconds,
            shell=False,
            sandbox_mode=engine.config.verification.sandbox_mode,
            sandbox_image=engine.config.verification.sandbox_image,
            sandbox_allow_network=engine.config.verification.sandbox_allow_network,
            sandbox_cpus=engine.config.verification.sandbox_cpus,
            sandbox_memory=engine.config.verification.sandbox_memory,
            sandbox_pids=engine.config.verification.sandbox_pids,
        )
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        return proc.returncode
    except OSError as exc:
        print(f"VERITY: failed to execute action: {exc}", file=sys.stderr)
        return 127


def scan_assumptions(engine: Engine) -> list[Assumption]:
    """Find unsupported assumptions baked into the current working tree."""
    engine.refresh()
    findings: list[Assumption] = []
    if not engine.changes.is_dirty:
        for claim in engine.repo.claims():
            if claim.status in {"unverified", "partially_verified"}:
                findings.append(
                    Assumption(
                        kind="unverified_claim",
                        description=claim.text,
                        severity=Risk.LOW,
                        evidence="No conclusive evidence attached.",
                        hint="Add an evidence contract or attach a verifier.",
                    )
                )
        return findings

    for change in engine.changes.files:
        path = change.path
        if path.endswith(".py"):
            missing = _unresolved_imports(engine.root, path)
            for module in missing:
                findings.append(
                    Assumption(
                        kind="missing_dependency",
                        description=f"`{path}` imports `{module}`, which is not available",
                        severity=Risk.HIGH,
                        evidence=f"Module `{module}` is neither local nor installed",
                        hint=f"Install `{module}` or remove the import.",
                        path=path,
                    )
                )
        if path.endswith((".yaml", ".yml", ".json", ".toml")):
            from .evidence.files import _parse_config

            full = engine.root / path
            if full.is_file():
                try:
                    _parse_config(full)
                except Exception as exc:
                    findings.append(
                        Assumption(
                            kind="invalid_config",
                            description=f"`{path}` is not valid {full.suffix.lstrip('.')}",
                            severity=Risk.HIGH,
                            evidence=f"{type(exc).__name__}: {exc}",
                            hint="Fix the configuration syntax.",
                            path=path,
                        )
                    )
    return findings
