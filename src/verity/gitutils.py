"""Git integration built on the git CLI (no GitPython dependency)."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .util import MAX_FILE_BYTES, read_text, run_command

GIT = ["git", "-c", "core.quotepath=false", "-c", "advice.detachedHead=false"]


def _git(root: Path, *args: str, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*GIT, *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def toplevel(root: Path) -> Path | None:
    """Return the git work-tree root, or None when not inside a repository."""
    try:
        proc = _git(root, "rev-parse", "--show-toplevel", timeout=10.0)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    if not value:
        return None
    try:
        return Path(value).resolve()
    except OSError:
        return None


def is_git_repo(root: Path) -> bool:
    """True only when *root itself* is the work-tree root.

    A project nested inside a larger repository (for example a package inside a
    monorepo or a temp directory under the user's home repo) must not cause
    VERITY to scan the parent repository. That scan can be enormous and is not
    the agent's project reality. Nested projects get git features by running
    ``git init`` in their own root.
    """
    top = toplevel(root)
    if top is None:
        return False
    try:
        return top == Path(root).resolve()
    except OSError:
        return False


def head_sha(root: Path) -> str | None:
    try:
        proc = _git(root, "rev-parse", "HEAD")
        return proc.stdout.strip() if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def short_sha(root: Path, length: int = 7) -> str:
    sha = head_sha(root)
    return sha[:length] if sha else "no-commit"


def branch(root: Path) -> str | None:
    try:
        proc = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
        return proc.stdout.strip() if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass
class FileChange:
    path: str
    status: str = "modified"  # added | modified | deleted | renamed
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    binary: bool = False
    untracked: bool = False

    @property
    def added_text(self) -> str:
        return "\n".join(self.added_lines)


@dataclass
class Changes:
    files: list[FileChange] = field(default_factory=list)
    raw_diff: str = ""
    commit: str | None = None
    base: str = "HEAD"
    is_repo: bool = True

    @property
    def is_dirty(self) -> bool:
        return bool(self.files)

    def by_path(self) -> dict[str, FileChange]:
        return {f.path: f for f in self.files}

    def added_lines(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for change in self.files:
            for line in change.added_lines:
                out.append((change.path, line))
        return out

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]


_STATUS_MAP = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "T": "modified",
}


def _parse_porcelain(root: Path) -> list[tuple[str, str]]:
    """Return (status, path) for every changed/untracked path."""
    try:
        proc = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    entries: list[tuple[str, str]] = []
    raw = proc.stdout.split("\0")
    i = 0
    while i < len(raw):
        entry = raw[i]
        if not entry:
            i += 1
            continue
        code = entry[:2]
        path = entry[3:]
        status = "untracked" if code == "??" else _STATUS_MAP.get(code.strip()[:1], "modified")
        if code.startswith("R") or code.startswith("C"):
            # Renames encode "old -> new" in the next NUL-separated token.
            if i + 1 < len(raw) and raw[i + 1]:
                path = raw[i + 1]
                i += 1
        entries.append((status, path))
        i += 1
    return entries


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def _diff_for_path(root: Path, path: str, base: str) -> tuple[list[str], list[str], bool]:
    proc = _git(root, "diff", "--unified=0", base, "--", path)
    if proc.returncode != 0:
        return [], [], False
    added: list[str] = []
    removed: list[str] = []
    binary = "Binary files" in proc.stdout or "GIT binary patch" in proc.stdout
    in_hunk = False
    for line in proc.stdout.splitlines():
        if _HUNK.match(line):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
    return added, removed, binary


def _untracked_content(root: Path, path: str) -> tuple[list[str], bool]:
    p = root / path
    try:
        if not p.is_file():
            return [], False
        if p.stat().st_size > MAX_FILE_BYTES:
            return [], True
        data = p.read_bytes()
        if b"\0" in data[:8000]:
            return [], True
        return read_text(p).splitlines(), False
    except OSError:
        return [], False


def collect_changes(root: Path, base: str = "HEAD") -> Changes:
    root = Path(root)
    if not is_git_repo(root):
        return Changes(is_repo=False, base=base)

    changes = Changes(commit=head_sha(root), base=base)
    for status, path in _parse_porcelain(root):
        # VERITY's own runtime state is not part of the agent's software reality.
        normalized = path.replace("\\", "/")
        if normalized == ".verity" or normalized.startswith(".verity/") or normalized.startswith(".git/"):
            continue
        if status == "deleted":
            changes.files.append(FileChange(path=path, status="deleted"))
            continue
        if status == "untracked":
            lines, binary = _untracked_content(root, path)
            changes.files.append(
                FileChange(
                    path=path,
                    status="added",
                    added_lines=lines,
                    binary=binary,
                    untracked=True,
                )
            )
            continue
        added, removed, binary = _diff_for_path(root, path, base)
        changes.files.append(
            FileChange(path=path, status=status, added_lines=added, removed_lines=removed, binary=binary)
        )

    try:
        proc = _git(root, "diff", "--unified=0", base)
        if proc.returncode == 0:
            changes.raw_diff = proc.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return changes


def changed_files_since(root: Path, since: str) -> list[str]:
    try:
        proc = _git(root, "diff", "--name-only", since, "HEAD")
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def file_changed_since(root: Path, path: str, since: str) -> bool:
    try:
        proc = _git(root, "diff", "--quiet", since, "HEAD", "--", path)
        return proc.returncode != 0
    except (OSError, subprocess.SubprocessError):
        return True


def install_pre_commit_hook(root: Path) -> Path:
    """Install a hook that blocks commits with contradicted required evidence."""
    hooks = root / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "# Installed by `verity hook install`.\n"
        "verity verify --fail-on contradicted >/dev/null 2>&1 || {\n"
        "  echo 'VERITY: commit blocked — required claims are contradicted or stale.'\n"
        "  echo 'Run `verity status` to see what needs evidence.'\n"
        "  exit 1\n"
        "}\n",
        encoding="utf-8",
    )
    try:
        hook.chmod(0o755)
    except OSError:
        pass
    return hook
