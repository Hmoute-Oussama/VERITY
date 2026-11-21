"""Runtime verifiers: schemas, HTTP probes, commands, and non-verifiable claims."""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..models import Claim, EvidenceResult
from ..util import (
    CODE_EXTS,
    read_text,
    relpath,
    run_command,
    walk_files,
)
from .base import VerifyContext, error, fail, ok, warn
from .tests import is_allowlisted, is_dangerous

_TABLE_PATTERNS = [
    ("sql", re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"'\[]?(\w+)", re.I)),
    ("alembic", re.compile(r"\bop\.create_table\(\s*[\"'](\w+)[\"']")),
    ("alembic_column", re.compile(r"\bop\.add_column\(\s*[\"'](\w+)[\"']")),
    ("sqlalchemy", re.compile(r"__tablename__\s*=\s*[\"'](\w+)[\"']")),
    ("django", re.compile(r"db_table\s*=\s*[\"'](\w+)[\"']")),
    ("jpa", re.compile(r"@Table\(\s*name\s*=\s*[\"'](\w+)[\"']")),
    ("prisma", re.compile(r"^\s*model\s+(\w+)\s*\{", re.M)),
    ("typeorm", re.compile(r"@Entity\(\s*[\"'](\w+)[\"']")),
    ("sequelize", re.compile(r"\.define\(\s*[\"'](\w+)[\"']")),
    ("rails", re.compile(r"create_table\s+[\"'](\w+)[\"']")),
]


def _sqlite_url(ctx: VerifyContext) -> Path | None:
    url = ctx.config.database.get("url") or ctx.config.database.get("sqlite_path")
    if not url:
        return None
    if isinstance(url, str) and url.startswith("sqlite:///"):
        path = url.replace("sqlite:///", "", 1)
        candidate = Path(path)
        return candidate if candidate.is_absolute() else ctx.root / candidate
    if isinstance(url, str) and url.startswith("sqlite://"):
        path = url.replace("sqlite://", "", 1)
        candidate = Path(path)
        return candidate if candidate.is_absolute() else ctx.root / candidate
    return None


def _sqlite_schema(path: Path, table: str | None) -> dict[str, list[str]]:
    if not path.is_file():
        return {}
    conn = sqlite3.connect(str(path))
    try:
        if table:
            rows = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND lower(name)=lower(?)",
                (table,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        return {row[0]: _columns_from_sql(row[1] or "") for row in rows}
    finally:
        conn.close()


def _columns_from_sql(sql: str) -> list[str]:
    body = sql[sql.find("(") + 1 : sql.rfind(")")] if "(" in sql else ""
    columns: list[str] = []
    for part in body.split(","):
        token = part.strip().split()[0] if part.strip() else ""
        token = token.strip('"`[]')
        if token and token.upper() not in {"PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "CHECK"}:
            columns.append(token)
    return columns


class DatabaseSchemaVerifier:
    name = "database_schema"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        table = claim.args.get("table")
        columns = [str(c) for c in (claim.args.get("columns") or [])]
        migration = bool(claim.args.get("migration"))

        db_path = _sqlite_url(ctx)
        if db_path is not None and db_path.is_file():
            schema = _sqlite_schema(db_path, str(table) if table else None)
            rel = relpath(db_path, ctx.root)
            if table:
                match = next((name for name in schema if name.lower() == str(table).lower()), None)
                if match:
                    actual = schema[match]
                    missing = [c for c in columns if c.lower() not in {x.lower() for x in actual}]
                    if missing:
                        return fail(
                            f"Table `{table}` exists but is missing column(s): {', '.join(missing)}",
                            details=f"Columns: {', '.join(actual)}",
                            payload={"table": table, "columns": actual, "database": rel},
                            deps=[rel],
                        )
                    return ok(
                        f"Table `{table}` exists in the SQLite database",
                        details=f"Columns: {', '.join(actual) or 'unknown'}",
                        payload={"table": table, "columns": actual, "database": rel},
                        deps=[rel],
                    )
            elif schema:
                return ok(
                    f"Database schema inspected ({len(schema)} tables)",
                    details=", ".join(sorted(schema)[:20]),
                    payload={"tables": {k: v for k, v in list(schema.items())[:50]}, "database": rel},
                    deps=[rel],
                )
            if migration and "alembic_version" in schema:
                return ok(
                    "Migration state is present in the database",
                    details="Found alembic_version table.",
                    payload={"database": rel},
                    deps=[rel],
                )
            if table:
                return fail(
                    f"Table `{table}` does not exist in the SQLite database",
                    details=f"Existing tables: {', '.join(sorted(schema)) or 'none'}",
                    payload={"table": table, "database": rel},
                    deps=[rel],
                )

        # No live database: fall back to definitions in the codebase.
        definitions: dict[str, list[str]] = {}
        migration_only: list[str] = []
        deps: list[str] = []
        files = walk_files(ctx.root, exts=CODE_EXTS | {".prisma", ".sql"}, limit=3000)
        for path in files:
            text = read_text(path)
            if not text or (table and str(table).lower() not in text.lower()):
                if not table and not any(pattern.search(text) for _, pattern in _TABLE_PATTERNS):
                    continue
                if table:
                    continue
            rel = relpath(path, ctx.root)
            for kind, pattern in _TABLE_PATTERNS:
                for match in pattern.finditer(text):
                    name = match.group(1)
                    if kind in {"alembic", "alembic_column"}:
                        migration_only.append(f"{rel}: {kind} {name}")
                        continue
                    definitions.setdefault(name, []).append(f"{rel}: {kind}")
                    if rel not in deps:
                        deps.append(rel)

        if table:
            match = next((n for n in definitions if n.lower() == str(table).lower()), None)
            if match:
                locations = definitions[match]
                return ok(
                    f"Table `{table}` is defined in the codebase",
                    details="\n".join(locations[:8]) + "\n\nDefinition evidence only; it does not prove the migration was applied.",
                    payload={"table": table, "locations": locations[:20], "applied": False},
                    deps=deps,
                    confidence=0.7,
                )
            if migration_only:
                return warn(
                    f"Table `{table}` appears only in migration operations",
                    details="\n".join(migration_only[:8])
                    + "\n\nCannot confirm the migration was executed against a live database.",
                    payload={"table": table, "migration_only": migration_only[:20]},
                    deps=[],
                    confidence=0.45,
                )
            return fail(
                f"No definition of table `{table}` was found",
                details=f"Searched {len(files)} source file(s).",
                payload={"table": table, "files_searched": len(files)},
                confidence=0.85,
            )

        if migration:
            if migration_only:
                return warn(
                    "Migration files exist, but their application cannot be confirmed",
                    details="\n".join(migration_only[:8]),
                    payload={"migrations": migration_only[:20]},
                    confidence=0.4,
                )
            return fail(
                "No migration files were found",
                payload={"files_searched": len(files)},
            )

        if definitions:
            return ok(
                f"{len(definitions)} table definition(s) found in the codebase",
                details=", ".join(sorted(definitions)[:20]),
                payload={"tables": {k: v[:5] for k, v in list(definitions.items())[:50]}},
                deps=deps,
                confidence=0.7,
            )
        return fail(
            "No database schema definitions were found",
            details=f"Searched {len(files)} source file(s).",
            payload={"files_searched": len(files)},
        )


class DeploymentConfigVerifier:
    name = "deployment_config"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        paths = [str(p) for p in (claim.args.get("paths") or claim.args.get("path") or [])]
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            paths = ["compose.yaml", "compose.yml", "docker-compose.yml", "kubernetes", "k8s", "deploy"]
        files: list[Path] = []
        for pattern in paths:
            if "*" in pattern or "?" in pattern:
                for path in walk_files(ctx.root, exts={".yaml", ".yml", ".json"}, limit=3000):
                    if re.search(pattern.replace("*", ".*").replace("?", "."), relpath(path, ctx.root)):
                        files.append(path)
            else:
                candidate = ctx.root / pattern
                if candidate.is_dir():
                    files.extend(walk_files(candidate, exts={".yaml", ".yml", ".json"}, limit=3000))
                elif candidate.is_file():
                    files.append(candidate)
        if not files:
            return fail(
                "No deployment configuration was found",
                details="Checked: " + ", ".join(paths),
                payload={"checked": paths},
            )
        deps = sorted({relpath(path, ctx.root) for path in files})
        text = "\n".join(read_text(path) for path in files)
        if re.search(r"(?i)\b(kind:\s*(Deployment|StatefulSet|DaemonSet|Service)|services\s*:)", text):
            return ok(
                f"Deployment configuration detected in {len(files)} file(s)",
                details=", ".join(deps[:20]),
                payload={"files": deps, "deployed": False},
                deps=deps,
                confidence=0.7,
            )
        return warn(
            "Deployment files exist but no recognized runtime structure was found",
            details=", ".join(deps[:20]),
            payload={"files": deps},
            deps=deps,
            confidence=0.35,
        )


class HttpProbeVerifier:
    name = "http_probe"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        url = str(claim.args.get("url") or "")
        method = str(claim.args.get("method") or "GET").upper()
        expect = claim.args.get("expect_status") or claim.args.get("expect")
        if not url:
            return fail("No URL was specified for this HTTP claim")
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        allowed = [h.lower() for h in ctx.config.verification.http_allow_hosts]
        if host not in allowed:
            return warn(
                f"HTTP probe blocked: `{host}` is not an allowed host",
                details=(
                    "Add the host to verification.http_allow_hosts in .verity/config.yaml "
                    "if probing it is intentional."
                ),
                payload={"url": url, "host": host, "allowed": allowed},
                confidence=0.2,
            )
        request = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                status = response.status
                body = response.read(2000).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = exc.read(1000).decode("utf-8", "replace") if exc.fp else ""
        except Exception as exc:
            return fail(
                f"HTTP probe to {url} failed",
                details=f"{type(exc).__name__}: {exc}",
                payload={"url": url, "method": method},
                confidence=0.9,
            )

        if isinstance(expect, int):
            expected = [expect]
        elif isinstance(expect, list):
            expected = [int(x) for x in expect]
        else:
            expected = list(range(200, 400))
        payload = {"url": url, "method": method, "status": status, "expected": expected,
                   "body_preview": body[:500]}
        if status in expected:
            return ok(
                f"{method} {url} returned {status}",
                details=body[:800],
                payload=payload,
            )
        return fail(
            f"{method} {url} returned {status}, expected {expected}",
            details=body[:800],
            payload=payload,
        )


class CommandVerifier:
    name = "command"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        command = str(claim.args.get("command") or "").strip()
        if not command:
            return fail("No command was specified")
        dangerous = is_dangerous(command)
        if dangerous:
            return fail(
                "Refusing to execute a dangerous command",
                details=f"Matched safety rule `{dangerous}`.",
                payload={"command": command},
            )
        if not is_allowlisted(command, ctx):
            return warn(
                "Command is not allowlisted — not executed",
                details=f"Add `{command.split()[0]}` to verification.allowed_commands or enable allow_exec.",
                payload={"command": command},
                confidence=0.3,
            )
        timeout = float(ctx.config.verification.timeout_seconds or 300)
        result = run_command(
            command,
            ctx.root,
            timeout=timeout,
            sandbox_mode=ctx.config.verification.sandbox_mode,
            sandbox_image=ctx.config.verification.sandbox_image,
            sandbox_allow_network=ctx.config.verification.sandbox_allow_network,
        )
        payload = {
            "command": command,
            "returncode": result.returncode,
            "duration_seconds": result.duration_seconds,
        }
        if result.ok:
            return ok(
                f"Command exited 0: {command}",
                details=result.tail(ctx.config.verification.max_output_chars),
                payload=payload,
            )
        return fail(
            f"Command failed with exit code {result.returncode}: {command}",
            details=result.tail(ctx.config.verification.max_output_chars),
            payload=payload,
        )


class MigrationConsistencyVerifier:
    name = "migration_consistency"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        table = str(claim.args.get("table") or "").strip()
        if not table:
            return fail("No table was specified for migration consistency")
        definition_files = [
            path for path in walk_files(ctx.root, exts={".sql", ".prisma"}, limit=3000)
            if re.search(rf"(?i)\b{re.escape(table)}\b", read_text(path))
        ]
        migration_files = [
            path for path in walk_files(ctx.root, exts=CODE_EXTS | {".sql"}, limit=3000)
            if re.search(r"(?i)(alembic|django\.db|migration|migrations|create_table|create table)", read_text(path))
            and re.search(rf"(?i)\b{re.escape(table)}\b", read_text(path))
        ]
        definition_rel = [relpath(path, ctx.root) for path in definition_files]
        migration_rel = [relpath(path, ctx.root) for path in migration_files]
        deps = sorted(set(definition_rel + migration_rel))

        if definition_rel and migration_rel:
            return ok(
                f"Table `{table}` has both schema definition and migration evidence",
                details="Definition: " + ", ".join(definition_rel) + "\nMigration: " + ", ".join(migration_rel),
                payload={"table": table, "definitions": definition_rel, "migrations": migration_rel},
                deps=deps,
                confidence=0.8,
            )
        if migration_rel:
            return warn(
                f"Table `{table}` appears in migrations but no matching schema definition was found",
                payload={"table": table, "migrations": migration_rel},
                deps=migration_rel,
                confidence=0.55,
            )
        if definition_rel:
            return warn(
                f"Table `{table}` is defined in schema code but no matching migration was found",
                payload={"table": table, "definitions": definition_rel},
                deps=definition_rel,
                confidence=0.55,
            )
        return fail(
            f"Neither schema nor migration evidence was found for table `{table}`",
            payload={
                "table": table,
                "files_searched": len(
                    walk_files(
                        ctx.root,
                        exts=CODE_EXTS | {".sql", ".prisma"},
                        limit=3000,
                    )
                ),
            },
            confidence=0.85,
        )


class ManualVerifier:
    name = "manual"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        source = claim.args.get("attested_by") or "an agent"
        return warn(
            f"Claim is only attested by {source}, not independently verified",
            details="Agent statements are not evidence. Attach a verifier or a concrete probe.",
            payload={"attested_by": source},
            confidence=0.2,
        )


class NoneVerifier:
    name = "none"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        return warn(
            "No automated verifier exists for this claim — it remains an assumption",
            details=(
                "This is intentional. VERITY never treats an agent's assertion as proof. "
                "Define an evidence contract with a concrete verifier to close it."
            ),
            payload={"verifier": "none"},
            confidence=0.0,
        )


class UnknownVerifier:
    def __init__(self, name: str):
        self.name = name

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        return error(
            f"Unknown verifier `{self.name}`",
            details="Check .verity/contracts.yaml or claim arguments for a typo.",
            payload={"verifier": self.name},
        )
