"""Project configuration, stored in ``.verity/config.yaml``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .util import dump_yaml, load_yaml

VERITY_DIR = ".verity"
CONFIG_FILE = "config.yaml"
CONTRACTS_FILE = "contracts.yaml"
DB_FILE = "verity.db"
SNAPSHOT_FILE = "snapshot.md"

DEFAULT_ALLOWED_COMMANDS = [
    "pytest",
    "python -m pytest",
    "python3 -m pytest",
    "py.test",
    "npm test",
    "npm run test",
    "npm run lint",
    "pnpm test",
    "yarn test",
    "mvn test",
    "mvn -q test",
    "mvn verify",
    "gradle test",
    "./gradlew test",
    "go test",
    "cargo test",
    "dotnet test",
    "ruff check",
    "mypy",
    "tsc --noEmit",
    "npx tsc --noEmit",
]


@dataclass
class VerificationConfig:
    test_command: str = "auto"
    timeout_seconds: int = 300
    allow_exec: bool = False
    allowed_commands: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_COMMANDS))
    http_allow_hosts: list[str] = field(
        default_factory=lambda: ["localhost", "127.0.0.1", "::1", "0.0.0.0"]
    )
    max_output_chars: int = 6000
    security_scan: bool = True
    sandboxed_execution: bool = True
    reproducibility_environment: bool = True
    sandbox_mode: str = "safe-env"


@dataclass
class EvidenceConfig:
    ttl_seconds: int | None = None
    track_all_code: bool = True
    history_limit: int = 200


@dataclass
class PolicyConfig:
    strict: bool = False
    block_risk: str = "high"
    warn_risk: str = "medium"
    deny_commands: list[str] = field(default_factory=list)
    allow_commands: list[str] = field(default_factory=list)
    require_verification_before_push: bool = False


@dataclass
class TeamConfig:
    name: str = ""
    organization: str = ""
    evidence_server: str = ""
    require_signed_bundles: bool = False


@dataclass
class Config:
    version: int = 1
    project: str = ""
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    team: TeamConfig = field(default_factory=TeamConfig)
    database: dict[str, Any] = field(default_factory=dict)
    ignore: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def defaults(cls, project: str) -> "Config":
        return cls(project=project)

    @classmethod
    def load(cls, root: Path) -> "Config":
        root = Path(root)
        path = root / VERITY_DIR / CONFIG_FILE
        data = load_yaml(path) if path.exists() else None
        if not isinstance(data, dict):
            data = {}
        cfg = cls.defaults(data.get("project") or root.name)
        cfg.version = int(data.get("version", 1) or 1)
        cfg.raw = data

        verification = data.get("verification") or {}
        if isinstance(verification, dict):
            for key, value in verification.items():
                if hasattr(cfg.verification, key) and value is not None:
                    setattr(cfg.verification, key, value)

        evidence = data.get("evidence") or {}
        if isinstance(evidence, dict):
            for key, value in evidence.items():
                if hasattr(cfg.evidence, key):
                    setattr(cfg.evidence, key, value)

        policy = data.get("policy") or {}
        if isinstance(policy, dict):
            for key, value in policy.items():
                if hasattr(cfg.policy, key) and value is not None:
                    setattr(cfg.policy, key, value)
        team = data.get("team") or {}
        if isinstance(team, dict):
            for key, value in team.items():
                if hasattr(cfg.team, key) and value is not None:
                    setattr(cfg.team, key, value)

        if isinstance(data.get("database"), dict):
            cfg.database = data["database"]
        if isinstance(data.get("ignore"), list):
            cfg.ignore = [str(x) for x in data["ignore"]]
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "project": self.project,
            "verification": {
                "test_command": self.verification.test_command,
                "timeout_seconds": self.verification.timeout_seconds,
                "allow_exec": self.verification.allow_exec,
                "allowed_commands": self.verification.allowed_commands,
                "http_allow_hosts": self.verification.http_allow_hosts,
                "max_output_chars": self.verification.max_output_chars,
                "security_scan": self.verification.security_scan,
                "sandboxed_execution": self.verification.sandboxed_execution,
                "reproducibility_environment": self.verification.reproducibility_environment,
                "sandbox_mode": self.verification.sandbox_mode,
            },
            "evidence": {
                "ttl_seconds": self.evidence.ttl_seconds,
                "track_all_code": self.evidence.track_all_code,
                "history_limit": self.evidence.history_limit,
            },
            "policy": {
                "strict": self.policy.strict,
                "block_risk": self.policy.block_risk,
                "warn_risk": self.policy.warn_risk,
                "deny_commands": self.policy.deny_commands,
                "allow_commands": self.policy.allow_commands,
                "require_verification_before_push": self.policy.require_verification_before_push,
            },
            "team": {
                "name": self.team.name,
                "organization": self.team.organization,
                "evidence_server": self.team.evidence_server,
                "require_signed_bundles": self.team.require_signed_bundles,
            },
            "database": self.database,
            "ignore": self.ignore,
        }

    def save(self, root: Path) -> Path:
        path = Path(root) / VERITY_DIR / CONFIG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# VERITY project configuration\n"
            "# Commit this file. Runtime evidence lives in .verity/verity.db (gitignored).\n"
            + dump_yaml(self.to_dict()),
            encoding="utf-8",
        )
        return path

    @property
    def verity_dir(self) -> Path:
        return Path(VERITY_DIR)


def verity_dir(root: Path) -> Path:
    return Path(root) / VERITY_DIR


def db_path(root: Path) -> Path:
    return verity_dir(root) / DB_FILE


def contracts_path(root: Path) -> Path:
    return verity_dir(root) / CONTRACTS_FILE


def snapshot_path(root: Path) -> Path:
    return verity_dir(root) / SNAPSHOT_FILE


def find_project_root(start: Path | None = None) -> Path:
    """Nearest ancestor containing .verity/, else the current directory."""
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / VERITY_DIR).is_dir():
            return candidate
    return current


DEFAULT_CONTRACTS = """\
# VERITY Evidence Contracts
#
# A contract states what must be proven before an agent may call a task done.
# Every contract becomes a claim; VERITY refuses to report the task as verified
# until every required contract has independent, current evidence.
#
# Run: verity contract check

version: 1
task: example_task

contracts:
  - id: tests_pass
    claim: The test suite passes on the current working tree.
    verifier: test_execution
    required: true

  - id: no_secrets
    claim: No secrets are committed to the repository.
    verifier: secret_scan
    required: true
"""


DEFAULT_CONFIG_HEADER = """\
# VERITY configuration — commit this file.
"""


def scaffold_config(root: Path, project: str | None = None) -> Config:
    cfg = Config.defaults(project or Path(root).name)
    cfg.save(root)
    return cfg
