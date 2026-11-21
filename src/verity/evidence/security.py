"""Security verifiers: secret scanning and heuristic security analysis."""

from __future__ import annotations

import re
import json
from pathlib import Path

from ..models import Claim, EvidenceResult
from ..util import CODE_EXTS, CONFIG_EXTS, read_text, redact_secret, relpath, walk_files
from .base import VerifyContext, fail, ok, warn

SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "high"),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), "high"),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "high"),
    ("stripe_live_key", re.compile(r"\bsk_live_[A-Za-z0-9]{16,}\b"), "high"),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "high"),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "high"),
    ("jwt_token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "medium"),
    (
        "generic_secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|"
            r"client[_-]?secret|db[_-]?password|database[_-]?password)\b"
            r"\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']"
        ),
        "medium",
    ),
    (
        "password_literal",
        re.compile(r"(?i)\bpassword\b\s*[:=]\s*[\"']([^\"'\s]{6,})[\"']"),
        "medium",
    ),
]

PLACEHOLDERS = {
    "changeme",
    "change-me",
    "change_me",
    "example",
    "example-key",
    "dummy",
    "placeholder",
    "your-key",
    "your_key",
    "your-token",
    "your_token",
    "test",
    "testing",
    "secret",
    "password",
    "admin",
    "none",
    "null",
    "true",
    "false",
    "xxx",
    "***",
}

PLACEHOLDER_MARKERS = (
    "${",
    "{{",
    "<",
    "os.environ",
    "os.getenv",
    "process.env",
    "import.meta.env",
    "env(",
    "getenv",
    "example",
    "placeholder",
    "your-",
    "your_",
    "changeme",
    "xxxx",
    "****",
    "redacted",
    "dummy",
)


def _looks_placeholder(value: str) -> bool:
    v = value.strip().lower()
    if v in PLACEHOLDERS or len(v) < 6:
        return True
    return any(marker in v for marker in PLACEHOLDER_MARKERS)


def _scan_paths(ctx: VerifyContext, claim_paths: list[str] | None = None) -> list[str]:
    paths = [str(p) for p in (claim_paths or [])]
    if paths:
        return paths
    files = walk_files(ctx.root, exts=CODE_EXTS | CONFIG_EXTS | {".env", ".md", ".txt"}, limit=2500)
    out = []
    for path in files:
        if path.name in {
            "package-lock.json",
            "poetry.lock",
            "pnpm-lock.yaml",
            "yarn.lock",
            "Cargo.lock",
            "go.sum",
        }:
            continue
        if path.name.endswith((".min.js", ".map")):
            continue
        out.append(relpath(path, ctx.root))
    return out


class SecretScanVerifier:
    name = "secret_scan"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        if not ctx.config.verification.security_scan:
            return warn("Secret scanning is disabled in project configuration")
        paths = [str(p) for p in (claim.args.get("paths") or [])] or _scan_paths(ctx)
        if not paths:
            return warn("No files were available to scan for secrets")

        findings: list[dict[str, str]] = []
        deps: list[str] = []
        for rel in paths:
            path = ctx.root / rel
            if not path.is_file():
                continue
            deps.append(rel)
            try:
                text = read_text(path, limit=1_000_000)
            except OSError:
                continue
            if not text:
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if len(line) > 2000:
                    continue
                for name, pattern, severity in SECRET_PATTERNS:
                    match = pattern.search(line)
                    if not match:
                        continue
                    value = match.group(1) if match.groups() else match.group(0)
                    # Placeholder heuristics only make sense for generic
                    # key/value patterns. Known token formats (AKIA..., ghp_...)
                    # are high-confidence even if they contain "example".
                    if match.groups() and _looks_placeholder(value):
                        continue
                    findings.append(
                        {
                            "path": rel,
                            "line": str(line_no),
                            "kind": name,
                            "severity": severity,
                            "redacted": redact_secret(value),
                        }
                    )
                    break

        high = [f for f in findings if f["severity"] == "high"]
        payload = {"findings": findings[:50], "files_scanned": len(deps)}
        if high:
            return fail(
                f"{len(high)} high-severity secret(s) found in the working tree",
                details="\n".join(
                    f"{f['path']}:{f['line']} — {f['kind']} ({f['redacted']})" for f in high[:10]
                ),
                payload=payload,
                deps=deps,
                confidence=0.9,
            )
        if findings:
            return warn(
                f"{len(findings)} potential secret(s) found — review required",
                details="\n".join(
                    f"{f['path']}:{f['line']} — {f['kind']} ({f['redacted']})" for f in findings[:10]
                ),
                payload=payload,
                deps=deps,
                confidence=0.6,
            )
        return ok(
            f"No secrets detected in {len(deps)} file(s)",
            details="Pattern scan only; this is not a substitute for a dedicated secret scanner.",
            payload=payload,
            deps=deps,
            confidence=0.7,
        )


HASH_INDICATORS = [
    r"\bbcrypt\b",
    r"\bargon2\b",
    r"\bpasslib\b",
    r"\bpbkdf2\b",
    r"\bscrypt\b",
    r"\bgenerate_password_hash\b",
    r"\bBCryptPasswordEncoder\b",
    r"\bhashpw\b",
    r"\bcheck_password_hash\b",
]

WEAK_PATTERNS = [
    (r"\bmd5\s*\(\s*\w*password", "MD5 used for password hashing"),
    (r"\bsha1\s*\(\s*\w*password", "SHA1 used for password hashing"),
    (r"\bpassword\s*==\s*[\w\"']", "plaintext password comparison"),
    (r"[\w\"']\s*==\s*\w*password\b", "plaintext password comparison"),
    (r"(?i)\bpassword\s*=\s*[\"'][^\"']{3,}[\"']", "hardcoded password literal"),
]


class SecurityAnalysisVerifier:
    name = "security_analysis"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        prop = str(claim.args.get("property") or "security properties hold")
        files = walk_files(ctx.root, exts=CODE_EXTS, limit=2500)
        hashing_files: list[str] = []
        weak: list[str] = []
        deps: list[str] = []
        for path in files:
            text = read_text(path)
            if not text:
                continue
            rel = relpath(path, ctx.root)
            if any(re.search(pattern, text) for pattern in HASH_INDICATORS):
                hashing_files.append(rel)
                deps.append(rel)
            for pattern, label in WEAK_PATTERNS:
                for match in re.finditer(pattern, text):
                    line = text[: match.start()].count("\n") + 1
                    weak.append(f"{rel}:{line}: {label}")
                    if rel not in deps:
                        deps.append(rel)
                    break

        disclaimer = (
            "Heuristic static analysis only. It can find known anti-patterns; "
            "it is not a security review and cannot prove the absence of vulnerabilities."
        )
        payload = {
            "property": prop,
            "hashing_files": hashing_files[:20],
            "weak_findings": weak[:50],
            "files_scanned": len(files),
            "disclaimer": disclaimer,
        }
        if weak:
            return fail(
                f"Security anti-pattern(s) found while checking: {prop}",
                details="\n".join(weak[:10]) + "\n\n" + disclaimer,
                payload=payload,
                deps=deps,
                confidence=0.75,
            )
        if hashing_files:
            return ok(
                "Password hashing primitives were found in the implementation",
                details="\n".join(hashing_files[:10]) + "\n\n" + disclaimer,
                payload=payload,
                deps=deps,
                confidence=0.55,
            )
        return warn(
            f"No evidence found for the security property: {prop}",
            details=disclaimer,
            payload=payload,
            deps=deps,
            confidence=0.3,
        )


class SbomVerifier:
    name = "sbom"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        path = str(claim.args.get("path") or "sbom.json")
        package = str(claim.args.get("package") or claim.args.get("name") or "").strip().lower()
        sbom_path = ctx.root / path
        if not sbom_path.is_file():
            return fail(f"SBOM `{path}` does not exist", payload={"path": path})
        try:
            data = json.loads(read_text(sbom_path))
        except json.JSONDecodeError as exc:
            return fail(
                f"SBOM `{path}` is not valid JSON",
                details=f"line {exc.lineno}: {exc.msg}",
                payload={"path": path},
                deps=[path],
            )
        if not isinstance(data, dict):
            return fail("SBOM must contain a JSON object", payload={"path": path}, deps=[path])

        names: list[str] = []
        format_name = str(data.get("bomFormat") or ("SPDX-" + str(data.get("spdxVersion") or "") if data.get("spdxVersion") else "") or "unknown")
        if data.get("bomFormat") == "CycloneDX":
            for component in data.get("components") or []:
                if isinstance(component, dict):
                    names.append(str(component.get("name") or component.get("purl") or "").lower())
        elif isinstance(data.get("packages"), list):
            for item in data["packages"]:
                if isinstance(item, dict):
                    names.append(str(item.get("name") or item.get("SPDXID") or "").lower())

        names = sorted(set(name for name in names if name))
        if not names:
            return warn(
                f"SBOM `{path}` was parsed but no package components were found",
                details="Supported formats: CycloneDX components; SPDX packages.",
                payload={"path": path, "format": format_name},
                deps=[path],
                confidence=0.4,
            )
        if package:
            matches = [name for name in names if package in name or name in package]
            if not matches:
                return fail(
                    f"Package `{package}` was not found in SBOM `{path}`",
                    payload={"path": path, "package": package, "format": format_name},
                    deps=[path],
                    confidence=0.9,
                )
            return ok(
                f"Package `{package}` is represented in SBOM `{path}`",
                payload={"path": path, "package": package, "matches": matches, "format": format_name},
                deps=[path],
                confidence=1.0,
            )
        return ok(
            f"SBOM `{path}` contains {len(names)} package component(s)",
            payload={"path": path, "format": format_name, "packages": names[:200]},
            deps=[path],
            confidence=0.9,
        )
