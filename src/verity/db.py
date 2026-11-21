"""SQLite persistence for claims, evidence, dependencies and events."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .models import (
    Claim,
    ClaimDraft,
    ClaimStatus,
    Evidence,
    EvidenceResult,
    EvidenceStatus,
    new_id,
    stable_key,
    utcnow,
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS claims (
    id            TEXT PRIMARY KEY,
    text          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    verifier      TEXT NOT NULL,
    args          TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'unverified',
    required      INTEGER NOT NULL DEFAULT 0,
    source        TEXT NOT NULL DEFAULT 'agent',
    confidence    REAL NOT NULL DEFAULT 0.5,
    contract_id   TEXT,
    dedupe_key    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    meta          TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_claims_dedupe ON claims(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS idx_claims_contract ON claims(contract_id);

CREATE TABLE IF NOT EXISTS evidence (
    id                 TEXT PRIMARY KEY,
    claim_id           TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    verifier           TEXT NOT NULL,
    status             TEXT NOT NULL,
    summary            TEXT NOT NULL,
    details            TEXT NOT NULL DEFAULT '',
    payload            TEXT NOT NULL DEFAULT '{}',
    confidence         REAL NOT NULL DEFAULT 1.0,
    commit_sha         TEXT,
    valid              INTEGER NOT NULL DEFAULT 1,
    superseded         INTEGER NOT NULL DEFAULT 0,
    invalidated_reason TEXT,
    created_at         TEXT NOT NULL,
    checked_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_claim ON evidence(claim_id);
CREATE INDEX IF NOT EXISTS idx_evidence_valid ON evidence(valid, superseded);

CREATE TABLE IF NOT EXISTS evidence_deps (
    evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    digest      TEXT,
    PRIMARY KEY (evidence_id, path)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         TEXT PRIMARY KEY,
    type       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
"""

DB_SCHEMA_VERSION = 1


def migrate_repository(repo: "Repository") -> int:
    """Apply forward-only SQLite migrations and record the schema version."""
    repo._execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    rows = repo._query("SELECT value FROM meta WHERE key = 'db_schema_version'")
    if not rows:
        repo.set_meta("db_schema_version", str(DB_SCHEMA_VERSION))
        return DB_SCHEMA_VERSION
    try:
        current = int(rows[0]["value"])
    except (TypeError, ValueError):
        raise RuntimeError("VERITY evidence database has an invalid db_schema_version")
    if current > DB_SCHEMA_VERSION:
        repo.close()
        raise RuntimeError(
            f"Evidence database schema {current} is newer than supported schema "
            f"{DB_SCHEMA_VERSION}; upgrade VERITY"
        )
    if current < DB_SCHEMA_VERSION:
        for version in range(current + 1, DB_SCHEMA_VERSION + 1):
            _apply_migration(repo, version)
        repo.set_meta("db_schema_version", str(DB_SCHEMA_VERSION))
    return DB_SCHEMA_VERSION


def _apply_migration(repo: "Repository", version: int) -> None:
    if version == 1:
        repo._execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        return
    raise RuntimeError(f"No migration implementation exists for database schema {version}")


class Repository:
    """Thin, explicit repository over one SQLite database."""

    def __init__(self, path: Path, project: str):
        self.path = Path(path)
        self.project = project
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        migrate_repository(self)

    @property
    def schema_version(self) -> int:
        rows = self._query("SELECT value FROM meta WHERE key = 'db_schema_version'")
        if not rows:
            return 0
        try:
            return int(rows[0]["value"])
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Repository":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _execute(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            if isinstance(params, dict):
                cur = self._conn.execute(sql, params)
            else:
                cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    # ------------------------------------------------------------------ claims
    def upsert_claim(self, draft: ClaimDraft | Claim) -> Claim:
        claim = draft if isinstance(draft, Claim) else draft.to_claim()
        existing = self._query("SELECT * FROM claims WHERE dedupe_key = ?", (claim.dedupe_key,))
        if existing:
            current = Claim.from_row(existing[0])
            # Contracts / manual requirements can promote an existing claim.
            changed = False
            if claim.required and not current.required:
                current.required = True
                changed = True
            if claim.contract_id and current.contract_id != claim.contract_id:
                current.contract_id = claim.contract_id
                changed = True
            if claim.source == "contract" and current.source != "contract":
                current.source = "contract"
                changed = True
            if changed:
                current.updated_at = utcnow()
                self._execute(
                    "UPDATE claims SET required=?, contract_id=?, source=?, updated_at=? WHERE id=?",
                    (
                        int(current.required),
                        current.contract_id,
                        current.source,
                        current.updated_at,
                        current.id,
                    ),
                )
            return current

        row = claim.to_row()
        self._execute(
            """INSERT INTO claims
               (id, text, kind, verifier, args, status, required, source, confidence,
                contract_id, dedupe_key, created_at, updated_at, meta)
               VALUES (:id,:text,:kind,:verifier,:args,:status,:required,:source,:confidence,
                       :contract_id,:dedupe_key,:created_at,:updated_at,:meta)""",
            row,
        )
        return claim

    def get_claim(self, claim_id: str) -> Claim | None:
        rows = self._query("SELECT * FROM claims WHERE id = ?", (claim_id,))
        return Claim.from_row(rows[0]) if rows else None

    def find_by_dedupe_key(self, key: str) -> Claim | None:
        rows = self._query("SELECT * FROM claims WHERE dedupe_key = ?", (key,))
        return Claim.from_row(rows[0]) if rows else None

    def find_claim(self, text: str, verifier: str | None = None) -> Claim | None:
        rows = self._query("SELECT * FROM claims WHERE text = ?", (text,))
        for row in rows:
            claim = Claim.from_row(row)
            if verifier is None or claim.verifier == verifier:
                return claim
        return None

    def claims(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        contract_id: str | None = None,
        required_only: bool = False,
    ) -> list[Claim]:
        sql = "SELECT * FROM claims WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if contract_id:
            sql += " AND contract_id = ?"
            params.append(contract_id)
        if required_only:
            sql += " AND required = 1"
        sql += " ORDER BY created_at ASC, id ASC"
        return [Claim.from_row(r) for r in self._query(sql, params)]

    def delete_claim(self, claim_id: str) -> bool:
        cur = self._execute("DELETE FROM claims WHERE id = ?", (claim_id,))
        return cur.rowcount > 0

    def set_claim_status(self, claim_id: str, status: ClaimStatus | str) -> None:
        value = status.value if isinstance(status, ClaimStatus) else status
        self._execute(
            "UPDATE claims SET status = ?, updated_at = ? WHERE id = ?",
            (value, utcnow(), claim_id),
        )

    # ------------------------------------------------------------------ evidence
    def add_evidence(
        self,
        claim: Claim,
        result: EvidenceResult,
        commit_sha: str | None,
        deps: dict[str, str | None] | None = None,
    ) -> Evidence:
        evidence = result.as_evidence(claim, commit_sha)
        with self._lock:
            self._conn.execute(
                "UPDATE evidence SET superseded = 1 WHERE claim_id = ? AND superseded = 0",
                (claim.id,),
            )
            self._conn.execute(
                """INSERT INTO evidence
                   (id, claim_id, verifier, status, summary, details, payload, confidence,
                    commit_sha, valid, superseded, invalidated_reason, created_at, checked_at)
                   VALUES (:id,:claim_id,:verifier,:status,:summary,:details,:payload,:confidence,
                           :commit_sha,:valid,:superseded,:invalidated_reason,:created_at,:checked_at)""",
                {**evidence.to_row(), "superseded": 0},
            )
            if deps:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO evidence_deps (evidence_id, path, digest) VALUES (?,?,?)",
                    [(evidence.id, path, digest) for path, digest in deps.items()],
                )
            self._conn.commit()
        return evidence

    def evidence_for(self, claim_id: str, *, current_only: bool = True) -> list[Evidence]:
        sql = "SELECT * FROM evidence WHERE claim_id = ?"
        if current_only:
            sql += " AND superseded = 0"
        sql += " ORDER BY created_at DESC"
        return [Evidence.from_row(r) for r in self._query(sql, (claim_id,))]

    def all_evidence(self, *, current_only: bool = True, valid_only: bool = False) -> list[Evidence]:
        sql = "SELECT * FROM evidence WHERE 1=1"
        if current_only:
            sql += " AND superseded = 0"
        if valid_only:
            sql += " AND valid = 1"
        sql += " ORDER BY created_at DESC"
        return [Evidence.from_row(r) for r in self._query(sql)]

    def evidence_deps(self, evidence_id: str) -> dict[str, str | None]:
        rows = self._query(
            "SELECT path, digest FROM evidence_deps WHERE evidence_id = ?", (evidence_id,)
        )
        return {r["path"]: r["digest"] for r in rows}

    def invalidate_evidence(self, evidence_id: str, reason: str) -> None:
        self._execute(
            "UPDATE evidence SET valid = 0, invalidated_reason = ?, checked_at = ? WHERE id = ?",
            (reason, utcnow(), evidence_id),
        )

    def touch_evidence(self, evidence_id: str) -> None:
        self._execute("UPDATE evidence SET checked_at = ? WHERE id = ?", (utcnow(), evidence_id))

    def evidence_history(self, claim_id: str) -> list[Evidence]:
        return [Evidence.from_row(r) for r in self._query(
            "SELECT * FROM evidence WHERE claim_id = ? ORDER BY created_at DESC", (claim_id,)
        )]

    # ------------------------------------------------------------------ events
    def record_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self._execute(
            "INSERT INTO events (id, type, payload, created_at) VALUES (?,?,?,?)",
            (new_id("ev"), event_type, json.dumps(payload or {}, sort_keys=True), utcnow()),
        )

    def events(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,))
        return [
            {
                "id": r["id"],
                "type": r["type"],
                "payload": json.loads(r["payload"] or "{}"),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ aggregate
    def stats(self) -> dict[str, Any]:
        claims = self.claims()
        by_status: dict[str, int] = {s.value: 0 for s in ClaimStatus}
        for claim in claims:
            by_status[claim.status] = by_status.get(claim.status, 0) + 1
        current = self.all_evidence(current_only=True)
        return {
            "project": self.project,
            "claims": len(claims),
            "claims_by_status": by_status,
            "required_claims": sum(1 for c in claims if c.required),
            "evidence": len(current),
            "evidence_valid": sum(1 for e in current if e.valid),
            "evidence_invalidated": sum(1 for e in current if not e.valid),
            "evidence_failed": sum(
                1 for e in current if e.valid and e.status == EvidenceStatus.FAIL.value
            ),
            "contracts": len({c.contract_id for c in claims if c.contract_id}),
            "last_updated": max((c.updated_at for c in claims), default=None),
        }

    def project_digest_marker(self) -> str | None:
        rows = self._query("SELECT value FROM meta WHERE key = 'tree_digest'")
        return rows[0]["value"] if rows else None

    def set_meta(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO meta (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def open_repository(root: Path, project: str | None = None) -> Repository:
    from .config import Config, db_path

    root = Path(root)
    cfg = Config.load(root)
    return Repository(db_path(root), project or cfg.project or root.name)


def ensure_meta_table(repo: Repository) -> None:
    """Backward-compatible guard for databases created before meta existed."""
    repo._execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")  # noqa: SLF001
