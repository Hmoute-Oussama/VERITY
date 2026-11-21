"""Verifier registry. Names are stable because they appear in contracts."""

from __future__ import annotations

from ..models import Claim, EvidenceResult
from .base import VerifyContext, Verifier


def get_verifier(name: str) -> Verifier:
    from . import code, deps, env, files, runtime, security, tests

    registry: dict[str, Verifier] = {
        "code_symbol": code.SymbolVerifier(),
        "code_route": code.RouteVerifier(),
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
