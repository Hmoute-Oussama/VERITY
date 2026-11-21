"""Small, dependency-light utilities shared across VERITY."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".verity",
    "node_modules",
    ".venv",
    "venv",
    "env",
    ".tox",
    "dist",
    "build",
    "target",
    "out",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".idea",
    ".vscode",
    "coverage",
    "htmlcov",
    ".gradle",
    ".terraform",
    "vendor",
    ".cache",
}

CODE_EXTS = {
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".cs",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".swift",
    ".scala",
    ".sql",
}

CONFIG_EXTS = {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".env"}

MANIFEST_NAMES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "Pipfile",
    "poetry.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "go.mod",
    "Cargo.toml",
    "Gemfile",
    "composer.json",
}

MAX_FILE_BYTES = 2_000_000


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8", "replace"))


def is_ignored_path(path: Path, root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    return any(part in IGNORED_DIRS for part in rel.parts)


def walk_files(
    root: Path,
    *,
    exts: set[str] | None = None,
    limit: int = 20000,
    include_ignored: bool = False,
) -> list[Path]:
    """Deterministic, bounded repository walk."""
    root = Path(root)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        if not include_ignored:
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        else:
            dirnames.sort()
        for name in sorted(filenames):
            p = base / name
            if not include_ignored and is_ignored_path(p, root):
                continue
            if exts is not None and p.suffix.lower() not in exts:
                continue
            found.append(p)
            if len(found) >= limit:
                return found
    return found


def relpath(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_text(path: Path, limit: int = 200_000) -> str:
    try:
        # utf-8-sig strips a BOM, which Windows editors and PowerShell commonly
        # write. Without this, ast/tomllib/yaml reject otherwise valid files.
        with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def digest_files(root: Path, paths: Iterable[str]) -> dict[str, str | None]:
    """Map repo-relative paths to content digests (None means absent)."""
    out: dict[str, str | None] = {}
    for rel in sorted(set(paths)):
        p = (root / rel).resolve()
        try:
            p.relative_to(root.resolve())
        except ValueError:
            out[rel] = None
            continue
        out[rel] = sha256_file(p)
    return out


def tree_digest(root: Path, paths: Iterable[str]) -> str:
    h = hashlib.sha256()
    for rel, digest in digest_files(root, paths).items():
        h.update(rel.encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update((digest or "missing").encode())
        h.update(b"\x00")
    return h.hexdigest()


def code_files_for_digest(root: Path, limit: int = 3000) -> list[str]:
    """Files whose content can invalidate execution evidence."""
    paths = []
    for p in walk_files(root, exts=CODE_EXTS | CONFIG_EXTS, limit=limit):
        if p.name in MANIFEST_NAMES or p.suffix.lower() in CODE_EXTS:
            paths.append(relpath(p, root))
    return paths


@dataclass
class RunResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def tail(self, limit: int = 6000) -> str:
        text = (self.stdout or "") + (("\n" + self.stderr) if self.stderr else "")
        text = text.strip()
        if len(text) > limit:
            return "…\n" + text[-limit:]
        return text


def run_command(
    command: str | list[str],
    cwd: Path,
    timeout: float = 300.0,
    env: dict[str, str] | None = None,
    shell: bool | None = None,
) -> RunResult:
    import time

    if isinstance(command, list):
        shell = False if shell is None else shell
        display = " ".join(command)
    else:
        shell = True if shell is None else shell
        display = command

    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    # Sandboxed execution starts with the host environment stripped of common
    # credential-bearing variables. Explicit env values are opt-in.
    secret_prefixes = (
        "AWS_", "AZURE_", "GCP_", "GOOGLE_APPLICATION_", "GH_", "GITHUB_",
        "NPM_", "PYPI_", "OPENAI_", "ANTHROPIC_", "CODEX_", "COPILOT_",
    )
    merged_env = {
        key: value
        for key, value in merged_env.items()
        if not key.upper().startswith(secret_prefixes)
    }
    merged_env.setdefault("PYTHONUNBUFFERED", "1")
    # Never let a nested agent session think it is interactive.
    merged_env.setdefault("CI", "1")

    start = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            shell=shell,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=merged_env,
        )
        return RunResult(
            command=display,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration_seconds=round(time.monotonic() - start, 3),
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(
            command=display,
            returncode=124,
            stdout=(exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr=f"Timed out after {timeout}s",
            duration_seconds=round(time.monotonic() - start, 3),
            timed_out=True,
        )
    except FileNotFoundError as exc:
        return RunResult(
            command=display,
            returncode=127,
            stdout="",
            stderr=str(exc),
            duration_seconds=round(time.monotonic() - start, 3),
        )


def load_toml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes().decode("utf-8-sig")
    except OSError:
        return {}
    try:
        import tomllib  # type: ignore

        return tomllib.loads(raw)
    except Exception:
        pass
    try:
        import tomli  # type: ignore

        return tomli.loads(raw)
    except Exception:
        return {}


def load_json(path: Path) -> Any:
    try:
        return json.loads(read_text(path) or "null")
    except (json.JSONDecodeError, OSError):
        return None


def load_yaml(path: Path) -> Any:
    try:
        import yaml

        return yaml.safe_load(read_text(path))
    except Exception:
        return None


def dump_yaml(data: Any) -> str:
    import yaml

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def glob_paths(root: Path, pattern: str) -> list[Path]:
    """Glob relative to root, never escaping it."""
    if not pattern:
        return []
    p = Path(pattern)
    if p.is_absolute():
        try:
            pattern = p.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return []
    if any(ch in pattern for ch in "*?["):
        return sorted(root.glob(pattern))
    candidate = root / pattern
    if candidate.exists():
        return [candidate]
    return []


def truncate(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def slugify(text: str, limit: int = 60) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return (slug or "item")[:limit]


def normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def redact_secret(value: str, keep: int = 4) -> str:
    value = value.strip()
    if len(value) <= keep * 2:
        return "*" * len(value)
    return value[:keep] + "…" + value[-keep:]


def which(tool: str) -> str | None:
    return shutil.which(tool)


def python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def find_upwards(start: Path, names: Iterable[str], stop: Path | None = None) -> Path | None:
    current = start.resolve()
    stop = stop.resolve() if stop else None
    names = set(names)
    while True:
        for name in names:
            if (current / name).exists():
                return current / name
        if current == stop or current.parent == current:
            return None
        current = current.parent


@dataclass
class Counters:
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def get(self, key: str, default: int = 0) -> int:
        return self.counts.get(key, default)

    def as_dict(self) -> dict[str, int]:
        return dict(self.counts)


def chunks(items: list[Any], size: int) -> Iterator[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def matches_any(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)
