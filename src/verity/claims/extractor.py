"""Claim decomposition.

Turns natural-language agent statements, commands, and code changes into
individually verifiable claims. Deliberately deterministic and explainable:
no LLM is required to decide *what* must be proven. (An optional LLM adapter
can be layered on later, but the evidence chain stays inspectable.)

Every rule declares the verifier that is appropriate for the claim. This is the
heart of VERITY's thesis: a claim about a function's existence needs different
evidence than a claim about runtime behaviour or security.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..gitutils import Changes, FileChange
from ..models import ClaimDraft, ClaimKind
from ..util import CODE_EXTS, MANIFEST_NAMES

# ---------------------------------------------------------------------------
# Technology -> candidate package names. Used when an agent says "we use X".
# ---------------------------------------------------------------------------

KNOWN_TECH: dict[str, dict[str, Any]] = {
    "jwt": {"candidates": ["pyjwt", "python-jose", "jsonwebtoken", "jjwt"], "label": "JWT"},
    "pyjwt": {"candidates": ["pyjwt"], "label": "PyJWT"},
    "python-jose": {"candidates": ["python-jose"], "label": "python-jose"},
    "jsonwebtoken": {"candidates": ["jsonwebtoken"], "label": "jsonwebtoken"},
    "postgres": {"candidates": ["psycopg2-binary", "psycopg", "asyncpg", "pg", "postgresql"], "label": "PostgreSQL"},
    "postgresql": {"candidates": ["psycopg2-binary", "psycopg", "asyncpg", "pg", "postgresql"], "label": "PostgreSQL"},
    "redis": {"candidates": ["redis", "ioredis", "aioredis"], "label": "Redis"},
    "sqlalchemy": {"candidates": ["sqlalchemy"], "label": "SQLAlchemy"},
    "fastapi": {"candidates": ["fastapi"], "label": "FastAPI"},
    "flask": {"candidates": ["flask"], "label": "Flask"},
    "django": {"candidates": ["django"], "label": "Django"},
    "pydantic": {"candidates": ["pydantic"], "label": "Pydantic"},
    "celery": {"candidates": ["celery"], "label": "Celery"},
    "docker": {"candidates": ["docker", "docker-compose"], "label": "Docker"},
    "kubernetes": {"candidates": ["kubernetes"], "label": "Kubernetes"},
    "react": {"candidates": ["react"], "label": "React"},
    "next": {"candidates": ["next"], "label": "Next.js"},
    "next.js": {"candidates": ["next"], "label": "Next.js"},
    "nextjs": {"candidates": ["next"], "label": "Next.js"},
    "spring": {"candidates": ["spring-boot", "spring-boot-starter-web"], "label": "Spring"},
    "spring boot": {"candidates": ["spring-boot", "spring-boot-starter-web"], "label": "Spring Boot"},
    "hibernate": {"candidates": ["hibernate-core"], "label": "Hibernate"},
    "kafka": {"candidates": ["kafka-python", "kafkajs", "spring-kafka"], "label": "Kafka"},
    "rabbitmq": {"candidates": ["pika", "amqplib", "spring-rabbit"], "label": "RabbitMQ"},
    "mongodb": {"candidates": ["pymongo", "mongodb", "mongoose"], "label": "MongoDB"},
    "mongo": {"candidates": ["pymongo", "mongodb", "mongoose"], "label": "MongoDB"},
    "mysql": {"candidates": ["pymysql", "mysqlclient", "mysql2"], "label": "MySQL"},
    "sqlite": {"candidates": ["aiosqlite"], "label": "SQLite"},
    "bcrypt": {"candidates": ["bcrypt"], "label": "bcrypt"},
    "argon2": {"candidates": ["argon2-cffi"], "label": "Argon2"},
    "stripe": {"candidates": ["stripe"], "label": "Stripe"},
    "openai": {"candidates": ["openai"], "label": "OpenAI"},
    "anthropic": {"candidates": ["anthropic"], "label": "Anthropic"},
    "alembic": {"candidates": ["alembic"], "label": "Alembic"},
    "pytest": {"candidates": ["pytest"], "label": "pytest"},
}

FEATURE_NOUNS = {
    "authentication",
    "authorization",
    "auth",
    "login",
    "logout",
    "registration",
    "signup",
    "payment",
    "payments",
    "checkout",
    "billing",
    "search",
    "upload",
    "download",
    "notification",
    "notifications",
    "email",
    "caching",
    "cache",
    "queue",
    "worker",
    "scheduler",
    "cron",
    "websocket",
    "websockets",
    "api",
    "endpoint",
    "database",
    "schema",
    "migration",
    "validation",
    "middleware",
    "dashboard",
    "admin",
    "reporting",
    "pagination",
    "filtering",
    "export",
    "import",
    "integration",
    "monitoring",
    "logging",
    "metrics",
    "rate limiting",
    "rate-limiting",
    "refresh token",
    "refresh tokens",
    "password reset",
}

STDLIB = set(getattr(sys, "stdlib_module_names", set()))

FILE_EXT_RE = r"(?:ya?ml|json|toml|ini|cfg|env|py|pyi|js|jsx|mjs|ts|tsx|java|kt|go|rs|rb|php|cs|sql|md|txt|xml|gradle|properties|lock|sh|ps1|html|css|scss|dockerfile)"

_CODE_FENCE = re.compile(r"```([A-Za-z0-9_+-]*)\n(.*?)```", re.DOTALL)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_HTTP_URL = re.compile(r"https?://[^\s)>\]\"']+")


@dataclass
class Rule:
    name: str
    pattern: re.Pattern[str]
    build: Callable[[re.Match[str], str, str], list[ClaimDraft] | None]
    kind: str = ClaimKind.GENERIC.value


@dataclass
class Extraction:
    claims: list[ClaimDraft] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, claim: ClaimDraft | None) -> None:
        if claim:
            self.claims.append(claim)

    def extend(self, claims: Iterable[ClaimDraft | None]) -> None:
        for claim in claims:
            self.add(claim)

    def dedupe(self) -> list[ClaimDraft]:
        seen: dict[tuple[str, str, str], ClaimDraft] = {}
        for claim in self.claims:
            key = (
                claim.text.strip().lower(),
                claim.verifier,
                json.dumps(claim.args, sort_keys=True),
            )
            if key not in seen:
                seen[key] = claim
        return list(seen.values())


def _draft(
    text: str,
    *,
    kind: str,
    verifier: str,
    args: dict[str, Any] | None = None,
    source: str = "agent",
    confidence: float = 0.7,
    required: bool = False,
    meta: dict[str, Any] | None = None,
) -> ClaimDraft:
    return ClaimDraft(
        text=text.strip().rstrip("."),
        kind=kind,
        verifier=verifier,
        args=args or {},
        source=source,
        confidence=confidence,
        required=required,
        meta=meta or {},
    )


def _tech(name: str) -> dict[str, Any] | None:
    key = name.strip().lower().rstrip(".,;:")
    if key in KNOWN_TECH:
        return KNOWN_TECH[key]
    key = re.sub(r"\s+", " ", key)
    return KNOWN_TECH.get(key)


def _looks_like_package(word: str) -> bool:
    word = word.strip().strip("`'\".,;:")
    if not word or len(word) < 3 or len(word) > 60:
        return False
    if word.lower() in {"the", "a", "an", "this", "that", "it", "them", "tests", "test"}:
        return False
    if word.lower() in STDLIB:
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+-]*", word)) and (
        "-" in word or "." in word or "_" in word or word.isupper()
    )


def _test_command(sentence: str) -> str | None:
    lowered = sentence.lower()
    for cmd in ("mvn test", "mvn -q test", "gradle test", "./gradlew test", "npm test",
                "pnpm test", "yarn test", "go test ./...", "cargo test", "dotnet test"):
        if cmd in lowered:
            return cmd
    if "pytest" in lowered or "py.test" in lowered:
        return "pytest"
    return None


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _build_tests(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    command = _test_command(sentence)
    args: dict[str, Any] = {}
    if command:
        args["command"] = command
    return [
        _draft(
            "The test suite passes on the current working tree",
            kind=ClaimKind.TEST.value,
            verifier="test_execution",
            args=args,
            source=source,
            confidence=0.95,
        )
    ]


def _build_secrets(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    return [
        _draft(
            "No secrets are committed to the repository",
            kind=ClaimKind.SECURITY.value,
            verifier="secret_scan",
            args={},
            source=source,
            confidence=0.9,
        )
    ]


def _build_security(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    prop = "passwords are hashed" if "password" in sentence.lower() else "security properties hold"
    return [
        _draft(
            f"The implementation satisfies the security claim ({prop})",
            kind=ClaimKind.SECURITY.value,
            verifier="security_analysis",
            args={"property": prop},
            source=source,
            confidence=0.75,
            meta={"quoted": sentence.strip()},
        )
    ]


def _build_endpoint(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    method = match.groupdict().get("method")
    path = match.groupdict().get("path")
    if not path:
        return []
    method = (method or "").upper() or None
    label = f"{method} {path}" if method else path
    runtime = bool(re.search(r"\b(works?|returns?|responds?|serves?|validates?|live|running)\b", sentence, re.I))
    return [
        _draft(
            f"The endpoint {label} exists",
            kind=ClaimKind.EXISTENCE.value,
            verifier="code_route",
            args={"path": path, "method": method},
            source=source,
            confidence=0.9,
            meta={"runtime_claim": runtime, "quoted": sentence.strip()},
        )
    ]


def _build_named_endpoint(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    name = match.group("name").strip()
    if name.lower() in {"the", "an", "this", "that", "every", "each", "new", "http"}:
        return []
    return [
        _draft(
            f"The {name} endpoint exists",
            kind=ClaimKind.EXISTENCE.value,
            verifier="code_route",
            args={"path": f"/{name}", "method": None, "fuzzy": True},
            source=source,
            confidence=0.65,
        )
    ]


def _build_symbol(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    groups = match.groupdict()
    symbol = (groups.get("symbol") or "").strip()
    if not symbol:
        return []
    # "added JWT" is a dependency claim, not a symbol claim.
    if _tech(symbol) is not None:
        return []
    return [
        _draft(
            f"Symbol `{symbol}` exists in the codebase",
            kind=ClaimKind.EXISTENCE.value,
            verifier="code_symbol",
            args={"symbol": symbol},
            source=source,
            confidence=0.9,
        )
    ]


def _build_file(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    path = (match.groupdict().get("path") or "").strip().strip("`'\"")
    if not path:
        return []
    return [
        _draft(
            f"File `{path}` exists",
            kind=ClaimKind.EXISTENCE.value,
            verifier="file_exists",
            args={"paths": [path]},
            source=source,
            confidence=0.9,
        )
    ]


def _build_dependency(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    raw = (match.groupdict().get("pkg") or "").strip()
    if not raw:
        return []
    tech = _tech(raw)
    if tech:
        return [
            _draft(
                f"Dependency `{tech['label']}` is available to the project",
                kind=ClaimKind.DEPENDENCY.value,
                verifier="dependency",
                args={"name": tech["label"], "candidates": tech["candidates"]},
                source=source,
                confidence=0.85,
            )
        ]
    if not _looks_like_package(raw):
        return []
    return [
        _draft(
            f"Dependency `{raw}` is available to the project",
            kind=ClaimKind.DEPENDENCY.value,
            verifier="dependency",
            args={"name": raw, "candidates": [raw]},
            source=source,
            confidence=0.8,
        )
    ]


def _build_schema(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    table = (match.groupdict().get("table") or "").strip().strip("`'\"")
    if table:
        return [
            _draft(
                f"Database table `{table}` has the expected schema",
                kind=ClaimKind.SCHEMA.value,
                verifier="database_schema",
                args={"table": table},
                source=source,
                confidence=0.85,
            )
        ]
    return [
        _draft(
            "The database schema was updated successfully",
            kind=ClaimKind.SCHEMA.value,
            verifier="database_schema",
            args={},
            source=source,
            confidence=0.7,
        )
    ]


def _build_migration(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    return [
        _draft(
            "Database migrations were applied successfully",
            kind=ClaimKind.SCHEMA.value,
            verifier="database_schema",
            args={"migration": True},
            source=source,
            confidence=0.8,
        )
    ]


def _build_env(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    groups = match.groupdict()
    tech = (groups.get("tech") or "").strip()
    version = (groups.get("version") or "").strip() or None
    if not tech:
        return []
    label = re.sub(r"\s+", " ", tech).strip()
    return [
        _draft(
            f"Project uses {label}{(' ' + version) if version else ''}",
            kind=ClaimKind.ENVIRONMENT.value,
            verifier="environment",
            args={"technology": label, "version": version},
            source=source,
            confidence=0.85,
        )
    ]


def _build_runtime(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    port_match = re.search(r"\bport\s+(\d{2,5})\b", sentence, re.I)
    if port_match:
        port = int(port_match.group(1))
        return [
            _draft(
                f"Service is listening on port {port}",
                kind=ClaimKind.ENVIRONMENT.value,
                verifier="environment",
                args={"port": port},
                source=source,
                confidence=0.8,
            )
        ]
    url = _HTTP_URL.search(sentence)
    if url:
        return [
            _draft(
                f"Endpoint {url.group(0)} responds successfully",
                kind=ClaimKind.BEHAVIOR.value,
                verifier="http_probe",
                args={"url": url.group(0), "method": "GET"},
                source=source,
                confidence=0.8,
            )
        ]
    return [
        _draft(
            f"The service is running ({sentence.strip()})",
            kind=ClaimKind.ENVIRONMENT.value,
            verifier="environment",
            args={"running": True},
            source=source,
            confidence=0.6,
        )
    ]


def _build_completion(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    return [
        _draft(
            f"Task completion is justified: {sentence.strip()}",
            kind=ClaimKind.COMPLETION.value,
            verifier="none",
            args={},
            source=source,
            confidence=0.8,
            required=True,
            meta={"quoted": sentence.strip(), "completion_claim": True},
        )
    ]


def _build_behavior(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    feature = (match.groupdict().get("feature") or "").strip()
    text = (
        f"Feature `{feature}` was implemented"
        if feature
        else f"Behaviour holds: {sentence.strip()}"
    )
    return [
        _draft(
            text,
            kind=ClaimKind.BEHAVIOR.value,
            verifier="none",
            args={},
            source=source,
            confidence=0.6,
            meta={"quoted": sentence.strip()},
        )
    ]


def _build_import_statement(match: re.Match[str], sentence: str, source: str) -> list[ClaimDraft]:
    module = (match.groupdict().get("module") or "").split(".")[0]
    if not module or module in STDLIB or module.startswith("_"):
        return []
    if module.lower() in {"typing", "dataclasses", "pathlib", "os", "sys", "json", "re"}:
        return []
    return [
        _draft(
            f"Python module `{module}` is importable",
            kind=ClaimKind.DEPENDENCY.value,
            verifier="dependency",
            args={"name": module, "candidates": [module], "module": module},
            source=source,
            confidence=0.75,
        )
    ]


RULES: list[Rule] = [
    Rule(
        "tests_pass",
        re.compile(
            r"\b(?:all\s+(?:the\s+)?)?(?:unit\s+|integration\s+|e2e\s+|end-to-end\s+)?"
            r"tests?\s+(?:now\s+)?(?:pass(?:es|ed)?|are\s+passing|is\s+passing|succeed(?:s|ed)?|"
            r"are\s+green|is\s+green)\b",
            re.I,
        ),
        _build_tests,
        ClaimKind.TEST.value,
    ),
    Rule(
        "test_suite",
        re.compile(r"\b(?:test\s+suite|pytest|test\s+run)\b[^.\n]*\b(?:pass(?:es|ed)?|green|succeed(?:s|ed)?)\b", re.I),
        _build_tests,
        ClaimKind.TEST.value,
    ),
    Rule(
        "no_secrets",
        re.compile(r"\bno\s+secrets?\s+(?:were\s+|are\s+)?(?:committed|leaked|exposed|present)\b", re.I),
        _build_secrets,
        ClaimKind.SECURITY.value,
    ),
    Rule(
        "password_security",
        re.compile(
            r"\b(?:passwords?|credentials?|tokens?)\s+(?:are|is|were|get)\s+"
            r"(?:securely\s+)?(?:hashed|encrypted|salted)\b",
            re.I,
        ),
        _build_security,
        ClaimKind.SECURITY.value,
    ),
    Rule(
        "secure_implementation",
        re.compile(r"\b(?:is|are)\s+(?:now\s+)?secure\b|\bsecure\s+authentication\b", re.I),
        _build_security,
        ClaimKind.SECURITY.value,
    ),
    Rule(
        "endpoint_with_method",
        re.compile(
            r"\b(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+[`\"']?(?P<path>/[\w\-./{}:$]+)",
            re.I,
        ),
        _build_endpoint,
        ClaimKind.EXISTENCE.value,
    ),
    Rule(
        "endpoint_path",
        re.compile(r"[`\"']?(?P<path>/[\w\-./{}:$]+)[`\"']?\s+(?:endpoint|route|url)\b", re.I),
        _build_endpoint,
        ClaimKind.EXISTENCE.value,
    ),
    Rule(
        "named_endpoint",
        re.compile(r"\b(?:the\s+)?(?P<name>[a-z][a-z0-9_-]{2,30})\s+(?:endpoint|route)\b", re.I),
        _build_named_endpoint,
        ClaimKind.EXISTENCE.value,
    ),
    Rule(
        "symbol_explicit",
        re.compile(
            r"\b(?:implemented|added|created|defined|introduced|exposed|wrote|generated)\s+"
            r"(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:function|method|class|module|helper|service|"
            r"component|handler|route|model)\s+[`\"']?(?P<symbol>[A-Za-z_]\w*)",
            re.I,
        ),
        _build_symbol,
        ClaimKind.EXISTENCE.value,
    ),
    Rule(
        "symbol_call_exists",
        re.compile(
            r"`?(?P<symbol>[A-Za-z_]\w*(?:\.[A-Za-z_]\w+)*)\s*\(\s*\)`?\s*"
            r"(?:now\s+)?(?:exists|is\s+defined|was\s+added|has\s+been\s+added)",
            re.I,
        ),
        _build_symbol,
        ClaimKind.EXISTENCE.value,
    ),
    Rule(
        "symbol_named",
        re.compile(
            r"\b(?:function|method|class)\s+[`\"']?(?P<symbol>[A-Za-z_]\w*)[`\"']?\s+"
            r"(?:exists|is\s+defined|now\s+exists)",
            re.I,
        ),
        _build_symbol,
        ClaimKind.EXISTENCE.value,
    ),
    Rule(
        "symbol_generic",
        re.compile(
            r"\b(?:implemented|added|created|introduced|defined)\s+(?:the\s+|a\s+|an\s+)?"
            r"(?P<symbol>[A-Z][A-Za-z0-9_]{2,}|[a-z][a-z0-9]*_[a-z0-9_]+)\b"
        ),
        _build_symbol,
        ClaimKind.EXISTENCE.value,
    ),
    Rule(
        "file_claim",
        re.compile(
            r"\b(?:created|added|updated|modified|wrote|generated|patched)\s+(?:the\s+|a\s+|an\s+)?"
            r"[`\"']?(?P<path>[\w./\\-]+\." + FILE_EXT_RE + r")[`\"']?",
            re.I,
        ),
        _build_file,
        ClaimKind.EXISTENCE.value,
    ),
    Rule(
        "explicit_install",
        re.compile(
            r"\b(?:pip3?|poetry|uv)\s+(?:install|add)\s+[`\"']?(?P<pkg>[A-Za-z0-9_.\-]+)|"
            r"\b(?:npm|pnpm|yarn)\s+(?:install|add)\s+[`\"']?(?P<pkg2>[@A-Za-z0-9_./\-]+)",
            re.I,
        ),
        lambda m, s, src: _build_dependency(
            _remap_pkg(m), s, src
        ),
        ClaimKind.DEPENDENCY.value,
    ),
    Rule(
        "tech_usage",
        re.compile(
            r"\b(?:using|uses|use|integrated|integrates|introduced|added|installed|leveraging|"
            r"powered\s+by)\s+(?:the\s+|a\s+|an\s+)?(?P<pkg>[A-Za-z][\w.+-]*)\b",
            re.I,
        ),
        _build_dependency,
        ClaimKind.DEPENDENCY.value,
    ),
    Rule(
        "schema_table",
        re.compile(
            r"\b(?:migrated|updated|modified|altered|created|added)\s+(?:the\s+)?[`\"']?"
            r"(?P<table>\w+)[`\"']?\s+table\b",
            re.I,
        ),
        _build_schema,
        ClaimKind.SCHEMA.value,
    ),
    Rule(
        "schema_generic",
        re.compile(
            r"\b(?:database|db)\s+schema\s+(?:was\s+|has\s+been\s+)?"
            r"(?:updated|modified|migrated|created|changed)\b",
            re.I,
        ),
        _build_schema,
        ClaimKind.SCHEMA.value,
    ),
    Rule(
        "migration_applied",
        re.compile(
            r"\bmigrations?\s+(?:was\s+|were\s+|has\s+been\s+|have\s+been\s+)?"
            r"(?:applied|executed|ran|run|completed)\b",
            re.I,
        ),
        _build_migration,
        ClaimKind.SCHEMA.value,
    ),
    Rule(
        "environment_version",
        re.compile(
            r"\b(?:uses|requires|runs\s+on|targets|built\s+with|powered\s+by)\s+"
            r"(?P<tech>Python|Java|Node(?:\.js)?|Go|Rust|Spring\s+Boot|React|PostgreSQL|MySQL|"
            r"Docker|Kubernetes)\s*(?P<version>\d+(?:\.\d+)*)?",
            re.I,
        ),
        _build_env,
        ClaimKind.ENVIRONMENT.value,
    ),
    Rule(
        "runtime_state",
        re.compile(
            r"\b(?:database|db|server|service|api|application|app|postgres|postgresql|redis|"
            r"docker|container)\s+(?:is|are)\s+(?:now\s+)?(?:running|up|reachable|live|listening)\b"
            r"|\b(?:server|service|api)\s+is\s+(?:now\s+)?(?:running|up)\b"
            r"|\brunning\s+on\s+port\s+\d{2,5}\b",
            re.I,
        ),
        _build_runtime,
        ClaimKind.ENVIRONMENT.value,
    ),
    Rule(
        "completion",
        re.compile(
            r"\b(?:everything|all)\s+(?:works|is\s+working|passes|is\s+done|is\s+complete)\b"
            r"|\b(?:the\s+)?(?:task|implementation|feature|work)\s+(?:is\s+)?"
            r"(?:complete|completed|done|finished|ready)\b"
            r"|\bI(?:'m|\s+am)\s+(?:done|finished)\b",
            re.I,
        ),
        _build_completion,
        ClaimKind.COMPLETION.value,
    ),
    Rule(
        "feature_implemented",
        re.compile(
            r"\b(?:implemented|built|added|created|integrated|set\s+up|configured)\s+"
            r"(?:the\s+|a\s+|an\s+)?(?P<feature>[a-z][a-z0-9 _-]{2,40}?)"
            r"(?=\s+(?:using|with|via|and|to|for|in|on|by)\b|[,.;]|$)",
            re.I,
        ),
        _build_behavior,
        ClaimKind.BEHAVIOR.value,
    ),
    Rule(
        "import_statement",
        re.compile(
            r"\b(?:from\s+(?P<module>[\w.]+)\s+import|import\s+(?P<module2>[\w.]+))",
            re.I,
        ),
        lambda m, s, src: _build_import_statement(_remap_module(m), s, src),
        ClaimKind.DEPENDENCY.value,
    ),
]


def _remap_pkg(match: re.Match[str]) -> re.Match[str]:
    """Normalise the two capture-group variants in the install rule."""
    return match


def _remap_module(match: re.Match[str]) -> re.Match[str]:
    return match


def _groups(match: re.Match[str], name: str, alt: str) -> str:
    return match.groupdict().get(name) or match.groupdict().get(alt) or ""


def extract_from_text(text: str, source: str = "agent") -> list[ClaimDraft]:
    """Decompose agent prose into claims. Deterministic and explainable."""
    if not text:
        return []
    extraction = Extraction()

    # Code blocks are analysed for imports rather than treated as prose.
    for match in _CODE_FENCE.finditer(text):
        lang, code = match.group(1).lower(), match.group(2)
        if lang in {"python", "py"}:
            for line in code.splitlines():
                m = re.match(r"\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", line)
                if m:
                    module = m.group(1) or m.group(2)
                    top = module.split(".")[0]
                    if top and top not in STDLIB and not top.startswith("_"):
                        extraction.add(
                            _draft(
                                f"Python module `{top}` is importable",
                                kind=ClaimKind.DEPENDENCY.value,
                                verifier="dependency",
                                args={"name": top, "candidates": [top], "module": top},
                                source=source,
                                confidence=0.8,
                            )
                        )

    prose = _CODE_FENCE.sub("\n", text)
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(prose) if s.strip()]
    for sentence in sentences:
        if len(sentence) > 1200:
            sentence = sentence[:1200]
        matched_specific = False
        for rule in RULES:
            for match in rule.pattern.finditer(sentence):
                # install / import rules have alternate groups
                if rule.name == "explicit_install":
                    pkg = match.groupdict().get("pkg") or match.groupdict().get("pkg2")
                    if not pkg:
                        continue
                    fake = _FakeMatch(match, {"pkg": pkg})
                    built = _build_dependency(fake, sentence, source)
                elif rule.name == "import_statement":
                    module = match.groupdict().get("module") or match.groupdict().get("module2") or ""
                    fake = _FakeMatch(match, {"module": module})
                    built = _build_import_statement(fake, sentence, source)
                else:
                    built = rule.build(match, sentence, source)
                if not built:
                    continue
                # Reject generic "feature implemented" matches that are not
                # recognised product features; they produce noise.
                if rule.name == "feature_implemented":
                    feature = (match.groupdict().get("feature") or "").strip().lower()
                    if feature in {"login", "logout", "signup", "registration", "endpoint", "route"} and (
                        "endpoint" in sentence.lower() or "route" in sentence.lower()
                    ):
                        continue
                    words = [w.strip("-_") for w in re.split(r"[\s_-]+", feature) if w.strip("-_")]
                    if not any(word in FEATURE_NOUNS for word in words):
                        continue
                extraction.extend(built)
                if rule.name not in {"feature_implemented", "tech_usage", "import_statement"}:
                    matched_specific = True
    return extraction.dedupe()


class _FakeMatch:
    """Adapter so rules with alternate capture groups share one builder."""

    def __init__(self, original: re.Match[str], groups: dict[str, Any]):
        self._original = original
        self._groups = groups

    def groupdict(self) -> dict[str, Any]:
        merged = dict(self._original.groupdict())
        merged.update(self._groups)
        return merged

    def group(self, *args: Any) -> Any:
        return self._original.group(*args)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

_COMMAND_RULES: list[tuple[re.Pattern[str], Callable[[re.Match[str]], list[ClaimDraft]]]] = [
    (
        re.compile(r"\bdocker(?:-|\s+)compose\s+(?:up|build|run)\b", re.I),
        lambda m: [
            _draft(
                "A Docker Compose configuration exists",
                kind=ClaimKind.DEPLOYMENT.value,
                verifier="file_exists",
                args={"paths": ["compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"]},
                source="command",
                confidence=0.95,
            )
        ],
    ),
    (
        re.compile(r"\bdocker\s+build\b", re.I),
        lambda m: [
            _draft(
                "A Dockerfile exists",
                kind=ClaimKind.DEPLOYMENT.value,
                verifier="file_exists",
                args={"paths": ["Dockerfile", "dockerfile", "Containerfile"]},
                source="command",
                confidence=0.95,
            )
        ],
    ),
    (
        re.compile(r"\bnpm\s+run\s+([\w:-]+)", re.I),
        lambda m: [
            _draft(
                f"npm script `{m.group(1)}` is defined",
                kind=ClaimKind.CONFIG.value,
                verifier="json_path",
                args={"path": "package.json", "json_path": f"scripts.{m.group(1)}"},
                source="command",
                confidence=0.95,
            )
        ],
    ),
    (
        re.compile(r"\bmvnw?\s+(?:-\S+\s+)*(?:test|verify|package|compile)\b", re.I),
        lambda m: [
            _draft(
                "A Maven project descriptor exists",
                kind=ClaimKind.CONFIG.value,
                verifier="file_exists",
                args={"paths": ["pom.xml"]},
                source="command",
                confidence=0.95,
            )
        ],
    ),
    (
        re.compile(r"\b(?:gradle|\./gradlew)\s+(?:test|build)\b", re.I),
        lambda m: [
            _draft(
                "A Gradle build descriptor exists",
                kind=ClaimKind.CONFIG.value,
                verifier="file_exists",
                args={"paths": ["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"]},
                source="command",
                confidence=0.95,
            )
        ],
    ),
    (
        re.compile(r"\bgo\s+test\b", re.I),
        lambda m: [
            _draft(
                "A Go module descriptor exists",
                kind=ClaimKind.CONFIG.value,
                verifier="file_exists",
                args={"paths": ["go.mod"]},
                source="command",
                confidence=0.95,
            )
        ],
    ),
    (
        re.compile(r"\bcargo\s+(?:test|build)\b", re.I),
        lambda m: [
            _draft(
                "A Cargo manifest exists",
                kind=ClaimKind.CONFIG.value,
                verifier="file_exists",
                args={"paths": ["Cargo.toml"]},
                source="command",
                confidence=0.95,
            )
        ],
    ),
    (
        re.compile(r"\b(?:python3?|py)\s+([\w./\\-]+\.py)\b", re.I),
        lambda m: [
            _draft(
                f"Script `{m.group(1)}` exists",
                kind=ClaimKind.EXISTENCE.value,
                verifier="file_exists",
                args={"paths": [m.group(1)]},
                source="command",
                confidence=0.95,
            )
        ],
    ),
    (
        re.compile(r"\b(?:pytest|py\.test)\s+([\w./\\-]+)", re.I),
        lambda m: [
            _draft(
                f"Test path `{m.group(1)}` exists",
                kind=ClaimKind.EXISTENCE.value,
                verifier="file_exists",
                args={"paths": [m.group(1)]},
                source="command",
                confidence=0.95,
            )
        ],
    ),
]


def extract_from_commands(commands: Iterable[str]) -> list[ClaimDraft]:
    extraction = Extraction()
    for command in commands:
        for pattern, builder in _COMMAND_RULES:
            match = pattern.search(command)
            if match:
                extraction.extend(builder(match))
    return extraction.dedupe()


# ---------------------------------------------------------------------------
# Code changes
# ---------------------------------------------------------------------------

_PY_DEF = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)")
_PY_CLASS = re.compile(r"^\s*class\s+([A-Za-z_]\w*)")
_PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))")
_FASTAPI_ROUTE = re.compile(
    r"^\s*@\s*[\w.]+\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']", re.I
)
_FLASK_ROUTE = re.compile(r"^\s*@\s*[\w.]+\.route\(\s*[\"']([^\"']+)[\"'](.*)$")
_JAVA_ROUTE = re.compile(
    r"^\s*@(Get|Post|Put|Patch|Delete)Mapping\(\s*(?:value\s*=\s*)?[\"']([^\"']+)[\"']"
)
_JS_ROUTE = re.compile(
    r"\b(?:app|router|server)\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']", re.I
)
_SQL_TABLE = re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"']?(\w+)", re.I)
_PRISMA_MODEL = re.compile(r"^\s*model\s+(\w+)\s*\{", re.M)
_JPA_TABLE = re.compile(r"@Table\(\s*name\s*=\s*[\"'](\w+)[\"']")
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*(?:[=<>!~^]=?.*)?$")


def _changed_path_claim(path: str) -> ClaimDraft:
    return _draft(
        f"File `{path}` exists",
        kind=ClaimKind.EXISTENCE.value,
        verifier="file_exists",
        args={"paths": [path]},
        source="diff",
        confidence=0.95,
    )


def _syntax_claim(path: str) -> ClaimDraft:
    return _draft(
        f"`{path}` parses without syntax errors",
        kind=ClaimKind.CONFIG.value,
        verifier="syntax",
        args={"paths": [path]},
        source="diff",
        confidence=0.9,
    )


def _imports_claim(path: str) -> ClaimDraft:
    return _draft(
        f"Imports in `{path}` resolve to installed or local modules",
        kind=ClaimKind.DEPENDENCY.value,
        verifier="imports",
        args={"paths": [path]},
        source="diff",
        confidence=0.9,
    )


def extract_from_changes(changes: Changes, root: Path) -> list[ClaimDraft]:
    """Claims implied by the agent's edits.

    These are not "the agent said so" claims: they are the assumptions baked
    into the change itself (the file exists, it parses, its imports resolve).
    """
    extraction = Extraction()
    if not changes.is_dirty:
        return []

    for change in changes.files:
        path = change.path
        suffix = Path(path).suffix.lower()
        name = Path(path).name

        if change.status == "deleted":
            extraction.add(
                _draft(
                    f"File `{path}` was deleted",
                    kind=ClaimKind.EXISTENCE.value,
                    verifier="file_exists",
                    args={"paths": [path], "expect": "absent"},
                    source="diff",
                    confidence=0.9,
                )
            )
            continue

        if change.status == "added":
            extraction.add(_changed_path_claim(path))

        if suffix == ".py":
            extraction.add(_syntax_claim(path))
            extraction.add(_imports_claim(path))
            for line in change.added_lines:
                m = _PY_DEF.match(line) or _PY_CLASS.match(line)
                if m:
                    extraction.add(
                        _draft(
                            f"Symbol `{m.group(1)}` exists in `{path}`",
                            kind=ClaimKind.EXISTENCE.value,
                            verifier="code_symbol",
                            args={"symbol": m.group(1), "path": path},
                            source="diff",
                            confidence=0.95,
                        )
                    )
                imp = _PY_IMPORT.match(line)
                if imp:
                    module = (imp.group(1) or imp.group(2) or "").split(".")[0]
                    if module and module not in STDLIB and not module.startswith("_"):
                        extraction.add(
                            _draft(
                                f"Python module `{module}` is importable",
                                kind=ClaimKind.DEPENDENCY.value,
                                verifier="dependency",
                                args={"name": module, "candidates": [module], "module": module},
                                source="diff",
                                confidence=0.85,
                            )
                        )
                route = _FASTAPI_ROUTE.match(line)
                if route:
                    method, route_path = route.group(1).upper(), route.group(2)
                    extraction.add(
                        _draft(
                            f"The endpoint {method} {route_path} exists",
                            kind=ClaimKind.EXISTENCE.value,
                            verifier="code_route",
                            args={"path": route_path, "method": method},
                            source="diff",
                            confidence=0.95,
                        )
                    )
                flask = _FLASK_ROUTE.match(line)
                if flask:
                    methods = re.findall(r"[\"']([A-Z]+)[\"']", flask.group(2) or "")
                    for method in methods or ["GET"]:
                        extraction.add(
                            _draft(
                                f"The endpoint {method} {flask.group(1)} exists",
                                kind=ClaimKind.EXISTENCE.value,
                                verifier="code_route",
                                args={"path": flask.group(1), "method": method},
                                source="diff",
                                confidence=0.95,
                            )
                        )

        elif suffix in {".java", ".kt"}:
            for line in change.added_lines:
                m = _JAVA_ROUTE.match(line)
                if m:
                    method, route_path = m.group(1).upper(), m.group(2)
                    extraction.add(
                        _draft(
                            f"The endpoint {method} {route_path} exists",
                            kind=ClaimKind.EXISTENCE.value,
                            verifier="code_route",
                            args={"path": route_path, "method": method},
                            source="diff",
                            confidence=0.95,
                        )
                    )

        elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            for line in change.added_lines:
                m = _JS_ROUTE.search(line)
                if m:
                    extraction.add(
                        _draft(
                            f"The endpoint {m.group(1).upper()} {m.group(2)} exists",
                            kind=ClaimKind.EXISTENCE.value,
                            verifier="code_route",
                            args={"path": m.group(2), "method": m.group(1).upper()},
                            source="diff",
                            confidence=0.9,
                        )
                    )

        elif suffix == ".sql":
            for line in change.added_lines:
                m = _SQL_TABLE.search(line)
                if m:
                    extraction.add(
                        _draft(
                            f"Database table `{m.group(1)}` has the expected schema",
                            kind=ClaimKind.SCHEMA.value,
                            verifier="database_schema",
                            args={"table": m.group(1)},
                            source="diff",
                            confidence=0.9,
                        )
                    )

        elif suffix == ".prisma":
            content = "\n".join(change.added_lines)
            for m in _PRISMA_MODEL.finditer(content):
                extraction.add(
                    _draft(
                        f"Database table `{m.group(1)}` has the expected schema",
                        kind=ClaimKind.SCHEMA.value,
                        verifier="database_schema",
                        args={"table": m.group(1)},
                        source="diff",
                        confidence=0.85,
                    )
                )

        if name in {"requirements.txt", "requirements-dev.txt", "constraints.txt"}:
            for line in change.added_lines:
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", "-", "git+", "http")):
                    continue
                m = _REQ_LINE.match(stripped)
                if m:
                    pkg = m.group(1)
                    extraction.add(
                        _draft(
                            f"Dependency `{pkg}` is declared by the project",
                            kind=ClaimKind.DEPENDENCY.value,
                            verifier="dependency",
                            args={"name": pkg, "candidates": [pkg], "declared_only": True},
                            source="diff",
                            confidence=0.9,
                        )
                    )

        if name == "pyproject.toml" and change.added_lines:
            for line in change.added_lines:
                m = re.match(r"^\s*[\"']([A-Za-z0-9][A-Za-z0-9_.\-]*)[\s\"'<>=!~]", line)
                if m:
                    pkg = m.group(1)
                    extraction.add(
                        _draft(
                            f"Dependency `{pkg}` is declared by the project",
                            kind=ClaimKind.DEPENDENCY.value,
                            verifier="dependency",
                            args={"name": pkg, "candidates": [pkg], "declared_only": True},
                            source="diff",
                            confidence=0.85,
                        )
                    )

        if name == "package.json":
            content = "\n".join(change.added_lines)
            for m in re.finditer(r"[\"'](@?[A-Za-z0-9_.\-]+)[\"']\s*:\s*[\"'][~^<>=]*\d", content):
                pkg = m.group(1)
                if pkg in {"name", "version", "main", "type", "license"}:
                    continue
                extraction.add(
                    _draft(
                        f"Dependency `{pkg}` is declared by the project",
                        kind=ClaimKind.DEPENDENCY.value,
                        verifier="dependency",
                        args={"name": pkg, "candidates": [pkg], "declared_only": True},
                        source="diff",
                        confidence=0.85,
                    )
                )

        if name in {"compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"}:
            extraction.add(
                _draft(
                    "The Docker Compose configuration is valid",
                    kind=ClaimKind.CONFIG.value,
                    verifier="config_valid",
                    args={"paths": [path]},
                    source="diff",
                    confidence=0.9,
                )
            )
        elif suffix in {".yaml", ".yml"} and name in {"application.yml", "application.yaml"}:
            extraction.add(
                _draft(
                    f"Configuration `{path}` is valid",
                    kind=ClaimKind.CONFIG.value,
                    verifier="config_valid",
                    args={"paths": [path]},
                    source="diff",
                    confidence=0.9,
                )
            )
        elif name in MANIFEST_NAMES and suffix == ".json":
            extraction.add(
                _draft(
                    f"Configuration `{path}` is valid JSON",
                    kind=ClaimKind.CONFIG.value,
                    verifier="config_valid",
                    args={"paths": [path]},
                    source="diff",
                    confidence=0.9,
                )
            )

    return extraction.dedupe()


def extract_from_transcript(path: Path) -> list[ClaimDraft]:
    """Best-effort extraction from JSONL or plain-text agent transcripts."""
    text = path.read_text(encoding="utf-8", errors="replace")
    pieces: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                pieces.append(line)
                continue
            pieces.extend(_collect_strings(data))
        else:
            pieces.append(line)
    return extract_from_text("\n".join(pieces), source="transcript")


def _collect_strings(data: Any, depth: int = 0) -> list[str]:
    if depth > 6:
        return []
    if isinstance(data, str):
        return [data] if len(data) > 8 else []
    if isinstance(data, list):
        out: list[str] = []
        for item in data:
            out.extend(_collect_strings(item, depth + 1))
        return out
    if isinstance(data, dict):
        out = []
        for key, value in data.items():
            if key.lower() in {"content", "text", "message", "parts", "output", "reasoning"}:
                out.extend(_collect_strings(value, depth + 1))
            elif key.lower() in {"role", "id", "type", "model", "timestamp"}:
                continue
            elif isinstance(value, (dict, list)):
                out.extend(_collect_strings(value, depth + 1))
        return out
    return []
