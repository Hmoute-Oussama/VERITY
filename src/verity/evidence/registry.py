"""Verifier registry. Names are stable because they appear in contracts."""

from __future__ import annotations

from ..models import Claim, EvidenceResult
from .base import VerifyContext, Verifier

VERIFIER_SPECS = {
    "code_symbol": {"kind": "existence", "args": ["symbol", "path"], "proof": "source definition found", "dependencies": "source files"},
    "code_route": {"kind": "existence", "args": ["path", "method", "fuzzy"], "proof": "source route declaration found", "dependencies": "source files"},
    "openapi": {"kind": "configuration", "args": ["path", "route", "method"], "proof": "OpenAPI route and optional method exists", "dependencies": "OpenAPI document"},
    "migration_consistency": {"kind": "schema", "args": ["table"], "proof": "matching schema and migration evidence", "dependencies": "SQL and migration files"},
    "lockfile_consistency": {"kind": "dependency", "args": ["manifest", "lockfile"], "proof": "lockfile exists and direct dependencies appear", "dependencies": "manifest and lockfile"},
    "sbom": {"kind": "security", "args": ["path", "package"], "proof": "CycloneDX or SPDX package representation", "dependencies": "SBOM document"},
    "database_schema": {"kind": "schema", "args": ["table", "columns", "migration"], "proof": "schema declaration or live SQLite structure", "dependencies": "database or source files"},
    "test_execution": {"kind": "test", "args": ["command", "paths"], "proof": "allowlisted command exits successfully", "dependencies": "test and source files"},
}


def verifier_catalog() -> dict[str, dict[str, object]]:
    """Return stable public metadata for every explicitly documented verifier."""
    return {name: dict(spec) for name, spec in VERIFIER_SPECS.items()}


def get_verifier(name: str) -> Verifier:
    from . import code, deps, env, files, runtime, security, tests

    registry: dict[str, Verifier] = {
        "code_symbol": code.SymbolVerifier(),
        "code_route": code.RouteVerifier(),
        "openapi": files.OpenApiVerifier(),
        "migration_consistency": runtime.MigrationConsistencyVerifier(),
        "lockfile_consistency": deps.LockfileConsistencyVerifier(),
        "sbom": security.SbomVerifier(),
        "syntax": code.SyntaxVerifier(),
        "imports": code.ImportsVerifier(),
        "file_exists": files.FileExistsVerifier(),
        "config_valid": files.ConfigValidVerifier(),
        "json_path": files.JsonPathVerifier(),
        "dependency": deps.DependencyVerifier(),
        "test_execution": tests.TestExecutionVerifier(),
        "environment": env.EnvironmentVerifier(),
        "secret_scan": security.SecretScanVerifier(),
        "security_analysis": security.SecurityAnalysisVerifier(),
        "database_schema": runtime.DatabaseSchemaVerifier(),
        "http_probe": runtime.HttpProbeVerifier(),
        "command": runtime.CommandVerifier(),
        "manual": runtime.ManualVerifier(),
        "none": runtime.NoneVerifier(),
    }
    if name not in registry:
        return runtime.UnknownVerifier(name)
    return registry[name]


def verify_claim(claim: Claim, ctx: VerifyContext) -> EvidenceResult:
    verifier = get_verifier(claim.verifier)
    try:
        return verifier.verify(claim, ctx)
    except Exception as exc:  # a verifier bug must never crash a verification run
        from .base import error

        return error(
            f"Verifier `{claim.verifier}` raised {type(exc).__name__}",
            details=str(exc),
            payload={"verifier": claim.verifier},
        )


__all__ = ["get_verifier", "verify_claim", "VerifyContext", "Verifier"]
