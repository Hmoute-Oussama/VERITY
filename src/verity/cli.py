"""VERITY command line interface."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _configure_stdio() -> None:
    """Windows consoles default to cp1252; VERITY's output is UTF-8."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass


_configure_stdio()

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __tagline__, __version__
from .claims.extractor import extract_from_text
from .config import (
    CONTRACTS_FILE,
    Config,
    contracts_path,
    find_project_root,
    scaffold_config,
    snapshot_path,
    verity_dir,
)
from .contracts import check_contracts, contract_summary, load_contracts, scaffold_contracts
from .engine import Engine, ProjectStatus
from .gitutils import install_pre_commit_hook, is_git_repo
from .graph import build_graph, to_dot, to_text
from .guard import check_action, execute_guarded, scan_assumptions
from .integrations import (
    TARGETS,
    IntegrationError,
    install,
    install_many,
    target_ids,
)
from .models import ClaimDraft, ClaimStatus, EvidenceStatus, Risk, Verdict
from .snapshot import build_agent_context, build_snapshot
from .util import dump_yaml, relpath
from .validation import validate_config, validate_contracts

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "VERITY — the reality layer for AI coding agents.\n\n"
        "No claim without evidence. No action without reality."
    ),
)
claim_app = typer.Typer(no_args_is_help=True, help="Inspect and manage individual claims.")
contract_app = typer.Typer(no_args_is_help=True, help="Evidence contracts: what must be proven.")
app.add_typer(claim_app, name="claim")
app.add_typer(contract_app, name="contract")

console = Console(highlight=False, soft_wrap=False, legacy_windows=False)
err_console = Console(stderr=True, highlight=False, legacy_windows=False)

STATUS_STYLE = {
    "verified": "green",
    "partially_verified": "yellow",
    "unverified": "bright_black",
    "contradicted": "red",
    "stale": "magenta",
}
EVIDENCE_STYLE = {
    "pass": "green",
    "warn": "yellow",
    "fail": "red",
    "error": "red",
    "stale": "magenta",
}
EVIDENCE_LABEL = {
    "pass": "PASS",
    "warn": "WARN",
    "fail": "FAIL",
    "error": "ERR ",
    "stale": "STALE",
}
OVERALL_LABEL = {
    "verified": ("Verification complete", "green"),
    "incomplete": ("Verification incomplete", "yellow"),
    "contradicted": ("Contradicted claims present", "red"),
    "stale": ("Evidence is stale", "magenta"),
    "no-claims": ("No claims recorded", "bright_black"),
}


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    root: Path | None = typer.Option(
        None, "--root", "-C", help="Project root (defaults to the nearest .verity directory)."
    ),
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    if version:
        console.print(f"VERITY {__version__} — {__tagline__}")
        raise typer.Exit(0)
    ctx.obj = {"root": Path(root).resolve() if root else find_project_root()}


def _root(ctx: typer.Context) -> Path:
    return (ctx.obj or {}).get("root") or find_project_root()


def _engine(ctx: typer.Context) -> Engine:
    root = _root(ctx)
    if not (verity_dir(root)).is_dir():
        err_console.print(
            f"[red]VERITY is not initialized in {escape(str(root))}.[/red]\n"
            "Run [bold]verity init[/bold] first."
        )
        raise typer.Exit(2)
    return Engine(root)


def _require_initialized(ctx: typer.Context) -> Path:
    root = _root(ctx)
    if not verity_dir(root).is_dir():
        err_console.print(
            f"[red]VERITY is not initialized in {escape(str(root))}.[/red]\n"
            "Run [bold]verity init[/bold] first."
        )
        raise typer.Exit(2)
    return root


def _print_header(title: str = "VERITY") -> None:
    console.print(f"[bold]{title}[/bold]  [bright_black]{__tagline__}[/bright_black]")


def _status_line(claim_status: str) -> Text:
    style = STATUS_STYLE.get(claim_status, "white")
    return Text(f"[{claim_status.replace('_', ' ').upper()}]", style=style)


def _render_status(status: ProjectStatus, *, show_evidence: bool = True) -> None:
    _print_header()
    console.print()
    console.print(f"Project: [bold]{escape(status.project)}[/bold]")
    commit = (status.commit or "no commit")[:10]
    branch = f" ({escape(status.branch)})" if status.branch else ""
    console.print(f"Commit:  {commit}{branch}")
    console.print()

    total = len(status.claims)
    c = status.counts
    rows = [
        ("Claims analyzed", total, "white"),
        ("Verified", c.get("verified", 0), "green"),
        ("Partially verified", c.get("partially_verified", 0), "yellow"),
        ("Unverified", c.get("unverified", 0), "bright_black"),
        ("Contradicted", c.get("contradicted", 0), "red"),
        ("Stale evidence", c.get("stale", 0), "magenta"),
    ]
    width = max(len(label) for label, _, _ in rows) + 2
    for label, value, style in rows:
        console.print(f"{label:<{width}} [{style}]{value:>3}[/{style}]")

    if show_evidence and status.claims:
        console.print()
        console.print("[bold]Evidence[/bold]")
        for claim in status.claims[:30]:
            evidence = next(
                (e for e in status.evidence if e.claim_id == claim.id and e.valid), None
            )
            if claim.status == ClaimStatus.CONTRADICTED.value and evidence:
                label, style, summary = "FAIL", "red", evidence.summary
            elif claim.status == ClaimStatus.VERIFIED.value and evidence:
                label, style, summary = "PASS", "green", evidence.summary
            elif claim.status == ClaimStatus.STALE.value:
                label, style, summary = "STALE", "magenta", "evidence invalidated by a code change"
            elif evidence:
                label, style, summary = "WARN", "yellow", evidence.summary
            else:
                label, style, summary = "OPEN", "bright_black", "no evidence attached"
            console.print(
                f"  [{style}][{label}][/{style}] {escape(claim.text)}"
                + (f" [bright_black]— {escape(summary)}[/bright_black]" if summary else "")
            )
        if len(status.claims) > 30:
            console.print(f"  [bright_black]… {len(status.claims) - 30} more[/bright_black]")

    console.print()
    console.print("[bold]Evidence integrity[/bold]")
    invalidated = sum(1 for e in status.evidence if not e.valid)
    if invalidated:
        console.print(
            f"  [magenta]{invalidated} evidence item(s) invalidated by code changes[/magenta]"
        )
        for inv in status.invalidations[:5]:
            console.print(f"  [bright_black]· {escape(inv.reason)}[/bright_black]")
    else:
        console.print("  [green]All stored evidence still matches the working tree.[/green]")
    if status.contracts_total:
        style = "green" if status.contracts_met == status.contracts_total else "yellow"
        console.print(
            f"  Contracts: [{style}]{status.contracts_met}/{status.contracts_total}[/{style}] satisfied"
        )

    label, style = OVERALL_LABEL.get(status.overall, (status.overall, "white"))
    console.print()
    console.print(f"Overall: [{style} bold]{label}[/{style} bold]")
    console.print()


def _status_dict(status: ProjectStatus) -> dict[str, Any]:
    return {
        "project": status.project,
        "commit": status.commit,
        "branch": status.branch,
        "overall": status.overall,
        "counts": status.counts,
        "contracts": {"met": status.contracts_met, "total": status.contracts_total},
        "evidence": {
            "total": len(status.evidence),
            "invalidated": sum(1 for e in status.evidence if not e.valid),
        },
        "claims": [
            {
                "id": c.id,
                "text": c.text,
                "status": c.status,
                "verifier": c.verifier,
                "required": c.required,
                "contract": c.contract_id,
            }
            for c in status.claims
        ],
        "invalidations": [
            {"claim_id": i.claim_id, "reason": i.reason, "path": i.path}
            for i in status.invalidations
        ],
    }


# ---------------------------------------------------------------------------
# init / status / analyze / verify
# ---------------------------------------------------------------------------


@app.command()
def init(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", help="Overwrite existing configuration."),
    no_gitignore: bool = typer.Option(False, "--no-gitignore", help="Do not touch .gitignore."),
) -> None:
    """Initialize VERITY in this project."""
    root = _root(ctx)
    root.mkdir(parents=True, exist_ok=True)
    vdir = verity_dir(root)
    vdir.mkdir(parents=True, exist_ok=True)

    cfg_path = vdir / "config.yaml"
    if force or not cfg_path.exists():
        cfg = scaffold_config(root)
        console.print(f"[green]created[/green] {relpath(cfg_path, root)}")
    else:
        cfg = Config.load(root)
        console.print(f"[bright_black]exists [/bright_black] {relpath(cfg_path, root)}")

    contracts = contracts_path(root)
    if force or not contracts.exists():
        scaffold_contracts(root, force=True)
        console.print(f"[green]created[/green] {relpath(contracts, root)}")

    engine = Engine(root, config=cfg)
    console.print(f"[green]created[/green] {relpath(engine.repo.path, root)}")

    if not no_gitignore:
        gitignore = root / ".gitignore"
        if gitignore.exists():
            text = gitignore.read_text(encoding="utf-8", errors="replace")
            if ".verity/*.db" not in text:
                with gitignore.open("a", encoding="utf-8") as fh:
                    if not text.endswith("\n"):
                        fh.write("\n")
                    fh.write("\n# VERITY local evidence store\n.verity/*.db\n.verity/*.db-wal\n.verity/*.db-shm\n")
                console.print("[green]updated[/green] .gitignore")

    console.print()
    console.print(
        Panel(
            "[bold]Next steps[/bold]\n\n"
            "  1. [bold]verity analyze \"I implemented authentication and tests pass\" --diff[/bold]\n"
            "  2. [bold]verity verify[/bold]\n"
            "  3. [bold]verity status[/bold]\n"
            "  4. [bold]verity contract check[/bold]\n"
            "  5. [bold]verity integrate --all[/bold]  → connect Claude Code, Codex, OpenCode and more\n\n"
            "[bright_black]Contracts live in .verity/contracts.yaml and should be committed.[/bright_black]",
            title="VERITY initialized",
            border_style="green",
        )
    )


@app.command()
def status(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Exit code only."),
) -> None:
    """Show the current reality status, invalidating stale evidence first."""
    engine = _engine(ctx)
    result = engine.status()
    if json_out:
        console.print_json(json.dumps(_status_dict(result), indent=2))
    elif not quiet:
        _render_status(result)
    raise typer.Exit(0 if result.overall in {"verified", "no-claims"} else 1)


@app.command()
def analyze(
    ctx: typer.Context,
    text: str | None = typer.Argument(None, help="Agent statement to decompose into claims."),
    file: Path | None = typer.Option(None, "--file", "-f", help="Read claims from a text file."),
    transcript: Path | None = typer.Option(None, "--transcript", help="Read an agent JSONL/text transcript."),
    command: list[str] | None = typer.Option(None, "--command", help="Agent command that was executed (repeatable)."),
    diff: bool = typer.Option(True, "--diff/--no-diff", help="Derive claims from the current git diff."),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="Run verifiers immediately."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Decompose agent statements (and the current diff) into verifiable claims."""
    engine = _engine(ctx)
    if file:
        text = (text or "") + "\n" + file.read_text(encoding="utf-8", errors="replace")
    if not text and not transcript and not command and not diff:
        err_console.print("[red]Nothing to analyze.[/red] Provide text, --file, --transcript, --command, or --diff.")
        raise typer.Exit(2)

    result = engine.analyze(
        text=text,
        transcript=transcript,
        commands=command,
        use_diff=diff,
        verify=verify,
    )
    if json_out:
        console.print_json(
            json.dumps(
                {
                    "claims": [
                        {"id": c.id, "text": c.text, "status": c.status, "verifier": c.verifier}
                        for c in result.claims
                    ],
                    "new": len(result.new_claims),
                    "verifications": [
                        {"claim_id": v.claim.id, "status": v.evidence.status, "summary": v.evidence.summary}
                        for v in result.verifications
                    ],
                    "notes": result.notes,
                },
                indent=2,
            )
        )
        return

    _print_header("VERITY analyze")
    console.print()
    if not result.claims:
        console.print("[yellow]No verifiable claims were found.[/yellow]")
        for note in result.notes:
            console.print(f"  [bright_black]{escape(note)}[/bright_black]")
        raise typer.Exit(0)

    console.print(
        f"Decomposed [bold]{len(result.claims)}[/bold] claim(s); "
        f"[green]{len(result.new_claims)}[/green] new.\n"
    )
    for claim in result.claims:
        current = engine.repo.get_claim(claim.id) or claim
        style = STATUS_STYLE.get(current.status, "white")
        console.print(f"  [{style}]●[/{style}] {escape(current.text)}")
        console.print(
            f"    [bright_black]verifier={current.verifier} kind={current.kind} "
            f"status={current.status.replace('_', ' ')}[/bright_black]"
        )
        if verify:
            evidence = engine.repo.evidence_for(current.id)
            if evidence:
                e = evidence[0]
                estyle = EVIDENCE_STYLE.get(e.status, "white")
                console.print(f"    [{estyle}]{e.summary}[/{estyle}]")
        console.print()
    if result.claims:
        status_result = engine.status()
        console.print(
            f"Overall: [bold]{OVERALL_LABEL.get(status_result.overall, (status_result.overall,))[0]}[/bold]"
        )


@app.command()
def verify(
    ctx: typer.Context,
    stale: bool = typer.Option(False, "--stale", help="Only re-verify stale or unverified claims."),
    claim_id: list[str] | None = typer.Option(None, "--claim", help="Verify a specific claim id (repeatable)."),
    fail_on: str = typer.Option(
        "contradicted",
        "--fail-on",
        help="Exit non-zero on: contradicted | stale | incomplete | never.",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run independent verifiers and record evidence."""
    engine = _engine(ctx)
    engine.invalidate_stale()
    verifications = engine.verify(claim_ids=claim_id, only_stale=stale)
    result = engine.status()

    if json_out:
        console.print_json(
            json.dumps(
                {
                    "verified": len(verifications),
                    "results": [
                        {
                            "claim_id": v.claim.id,
                            "claim": v.claim.text,
                            "evidence_status": v.evidence.status,
                            "summary": v.evidence.summary,
                            "valid": v.evidence.valid,
                        }
                        for v in verifications
                    ],
                    "status": _status_dict(result),
                },
                indent=2,
            )
        )
    else:
        _print_header("VERITY verify")
        console.print()
        if not verifications:
            console.print("[yellow]No claims to verify.[/yellow] Run [bold]verity analyze[/bold] first.")
        for v in verifications:
            style = EVIDENCE_STYLE.get(v.evidence.status, "white")
            label = EVIDENCE_LABEL.get(v.evidence.status, "?")
            console.print(f"[{style}][{label}][/{style}] {escape(v.claim.text)}")
            console.print(f"       [bright_black]{escape(v.evidence.summary)}[/bright_black]")
            if v.evidence.status in {"fail", "error"} and v.evidence.details:
                for line in v.evidence.details.splitlines()[:6]:
                    console.print(f"       [red]{escape(line)}[/red]")
        console.print()
        _render_status(result, show_evidence=False)

    code = _fail_code(result, fail_on)
    raise typer.Exit(code)


def _fail_code(status: ProjectStatus, fail_on: str) -> int:
    fail_on = fail_on.lower()
    if fail_on == "never":
        return 0
    if fail_on == "contradicted":
        return 1 if status.counts.get("contradicted", 0) else 0
    if fail_on == "stale":
        return 1 if status.counts.get("contradicted", 0) or status.counts.get("stale", 0) else 0
    if fail_on == "incomplete":
        return 0 if status.overall in {"verified", "no-claims"} else 1
    err_console.print(f"[red]Unknown --fail-on value: {escape(fail_on)}[/red]")
    return 2


# ---------------------------------------------------------------------------
# claims / evidence
# ---------------------------------------------------------------------------


@claim_app.command("list")
def claim_list(
    ctx: typer.Context,
    status_filter: str | None = typer.Option(None, "--status", help="Filter by claim status."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List claims and their latest evidence."""
    engine = _engine(ctx)
    claims = engine.repo.claims(status=status_filter)
    if json_out:
        console.print_json(
            json.dumps(
                [
                    {
                        "id": c.id,
                        "text": c.text,
                        "status": c.status,
                        "verifier": c.verifier,
                        "required": c.required,
                        "contract": c.contract_id,
                    }
                    for c in claims
                ],
                indent=2,
            )
        )
        return
    if not claims:
        console.print("[bright_black]No claims recorded.[/bright_black]")
        return
    table = Table(box=None, pad_edge=False, show_header=True, header_style="bold bright_black")
    table.add_column("Status", width=12)
    table.add_column("Claim", overflow="fold")
    table.add_column("Verifier", style="cyan", no_wrap=True)
    table.add_column("Evidence", overflow="fold", style="bright_black")
    for claim in claims:
        evidence = engine.repo.evidence_for(claim.id)
        summary = evidence[0].summary if evidence else ""
        style = STATUS_STYLE.get(claim.status, "white")
        table.add_row(
            f"[{style}]{claim.status.replace('_', ' ')}[/{style}]",
            escape(claim.text),
            claim.verifier,
            escape(summary),
        )
    console.print(table)


@claim_app.command("add")
def claim_add(
    ctx: typer.Context,
    text: str = typer.Argument(..., help="Claim text."),
    verifier: str = typer.Option("none", "--verifier", "-v", help="Verifier name (see README)."),
    arg: list[str] | None = typer.Option(None, "--arg", help="Verifier argument key=value (repeatable)."),
    required: bool = typer.Option(False, "--required", help="Require this claim for completion."),
    verify_now: bool = typer.Option(True, "--verify/--no-verify"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Add a single claim with an explicit verifier."""
    engine = _engine(ctx)
    args: dict[str, Any] = {}
    for item in arg or []:
        if "=" not in item:
            err_console.print(f"[red]Invalid --arg (expected key=value): {escape(item)}[/red]")
            raise typer.Exit(2)
        key, value = item.split("=", 1)
        parsed: Any = value
        if value.lower() in {"true", "false"}:
            parsed = value.lower() == "true"
        elif value.isdigit():
            parsed = int(value)
        elif "," in value:
            parsed = [v for v in value.split(",") if v]
        args[key.strip()] = parsed
    draft = ClaimDraft(
        text=text,
        verifier=verifier,
        args=args,
        required=required,
        source="manual",
        confidence=1.0,
    )
    claim, verifications = engine.add_claim(draft, verify=verify_now)
    if json_out:
        console.print_json(
            json.dumps(
                {
                    "id": claim.id,
                    "status": claim.status,
                    "text": claim.text,
                    "verifier": claim.verifier,
                    "evidence": [
                        {"status": v.evidence.status, "summary": v.evidence.summary}
                        for v in verifications
                    ],
                },
                indent=2,
            )
        )
        return
    style = STATUS_STYLE.get(claim.status, "white")
    console.print(f"[{style}]{claim.status.replace('_', ' ').upper()}[/{style}] {escape(claim.text)}")
    console.print(f"[bright_black]id={claim.id} verifier={claim.verifier}[/bright_black]")
    for v in verifications:
        estyle = EVIDENCE_STYLE.get(v.evidence.status, "white")
        console.print(f"  [{estyle}]{v.evidence.summary}[/{estyle}]")


@claim_app.command("show")
def claim_show(
    ctx: typer.Context,
    claim_id: str = typer.Argument(..., help="Claim id (or unique prefix)."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show a claim, its arguments, and full evidence history."""
    engine = _engine(ctx)
    claim = engine.repo.get_claim(claim_id)
    if claim is None:
        matches = [c for c in engine.repo.claims() if c.id.startswith(claim_id)]
        if len(matches) == 1:
            claim = matches[0]
    if claim is None:
        err_console.print(f"[red]Claim not found: {escape(claim_id)}[/red]")
        raise typer.Exit(1)
    history = engine.repo.evidence_history(claim.id)
    if json_out:
        console.print_json(
            json.dumps(
                {
                    "id": claim.id,
                    "text": claim.text,
                    "status": claim.status,
                    "verifier": claim.verifier,
                    "args": claim.args,
                    "required": claim.required,
                    "contract": claim.contract_id,
                    "history": [
                        {
                            "id": e.id,
                            "status": e.status,
                            "valid": e.valid,
                            "superseded": e.superseded,
                            "summary": e.summary,
                            "details": e.details,
                            "reason": e.invalidated_reason,
                            "created_at": e.created_at,
                            "dependencies": engine.repo.evidence_deps(e.id),
                        }
                        for e in history
                    ],
                },
                indent=2,
            )
        )
        return
    style = STATUS_STYLE.get(claim.status, "white")
    console.print(f"[bold]{escape(claim.text)}[/bold]")
    console.print(f"Status:   [{style}]{claim.status}[/{style}]")
    console.print(f"ID:       {claim.id}")
    console.print(f"Verifier: {claim.verifier}")
    console.print(f"Kind:     {claim.kind}")
    console.print(f"Source:   {claim.source}")
    console.print(f"Required: {'yes' if claim.required else 'no'}")
    if claim.contract_id:
        console.print(f"Contract: {claim.contract_id}")
    console.print(f"Args:     {json.dumps(claim.args, sort_keys=True)}")
    console.print()
    console.print("[bold]Evidence history[/bold]")
    if not history:
        console.print("  [bright_black]none[/bright_black]")
    for e in history:
        estyle = EVIDENCE_STYLE.get(e.status, "white")
        state = "valid" if e.valid else f"invalid ({e.invalidated_reason})"
        if e.superseded:
            state = "superseded"
        console.print(
            f"  [{estyle}]{e.status.upper():5s}[/{estyle}] {escape(e.summary)} "
            f"[bright_black]({state}, {e.created_at})[/bright_black]"
        )
        if e.details and (e.status in {"fail", "error"} or not e.valid):
            for line in e.details.splitlines()[:8]:
                console.print(f"          [bright_black]{escape(line)}[/bright_black]")


@claim_app.command("rm")
def claim_rm(
    ctx: typer.Context,
    claim_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Delete a claim and all of its evidence."""
    engine = _engine(ctx)
    claim = engine.repo.get_claim(claim_id)
    if claim is None:
        err_console.print(f"[red]Claim not found: {escape(claim_id)}[/red]")
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"Delete claim '{claim.text}' and its evidence?", abort=True)
    engine.repo.delete_claim(claim.id)
    console.print(f"[green]deleted[/green] {claim.id}")


@app.command()
def evidence(
    ctx: typer.Context,
    claim_id: str | None = typer.Option(None, "--claim"),
    all_history: bool = typer.Option(False, "--history", help="Include superseded evidence."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List evidence records and their validity."""
    engine = _engine(ctx)
    if claim_id:
        items = (
            engine.repo.evidence_history(claim_id)
            if all_history
            else engine.repo.evidence_for(claim_id)
        )
    else:
        items = engine.repo.all_evidence(current_only=not all_history)
    if json_out:
        console.print_json(
            json.dumps(
                [
                    {
                        "id": e.id,
                        "claim_id": e.claim_id,
                        "verifier": e.verifier,
                        "status": e.status,
                        "valid": e.valid,
                        "superseded": e.superseded,
                        "summary": e.summary,
                        "reason": e.invalidated_reason,
                        "created_at": e.created_at,
                    }
                    for e in items
                ],
                indent=2,
            )
        )
        return
    if not items:
        console.print("[bright_black]No evidence recorded.[/bright_black]")
        return
    table = Table(box=None, pad_edge=False, header_style="bold bright_black")
    table.add_column("Status", width=8)
    table.add_column("Verifier", style="cyan", no_wrap=True)
    table.add_column("Summary", overflow="fold")
    table.add_column("State", width=12)
    for e in items:
        style = EVIDENCE_STYLE.get(e.status, "white")
        state = "valid" if e.valid else "invalidated"
        if e.superseded:
            state = "superseded"
        table.add_row(
            f"[{style}]{e.status}[/{style}]",
            e.verifier,
            escape(e.summary),
            state,
        )
    console.print(table)


@app.command()
def invalidate(
    ctx: typer.Context,
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would be invalidated."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Mark evidence stale when the files it depends on have changed."""
    engine = _engine(ctx)
    invalidations = engine.invalidate_stale(dry_run=dry_run)
    if json_out:
        console.print_json(
            json.dumps(
                [
                    {
                        "evidence_id": i.evidence_id,
                        "claim_id": i.claim_id,
                        "claim": i.claim_text,
                        "path": i.path,
                        "reason": i.reason,
                    }
                    for i in invalidations
                ],
                indent=2,
            )
        )
        return
    if not invalidations:
        console.print("[green]No stale evidence. Everything still matches the working tree.[/green]")
        return
    verb = "would invalidate" if dry_run else "invalidated"
    console.print(f"[magenta]{verb} {len(invalidations)} evidence item(s):[/magenta]")
    for inv in invalidations:
        console.print(f"  · {escape(inv.claim_text)}")
        console.print(f"    [bright_black]{escape(inv.reason)}[/bright_black]")
    if not dry_run:
        console.print("\nRun [bold]verity verify --stale[/bold] to refresh them.")


# ---------------------------------------------------------------------------
# snapshot / graph / contracts
# ---------------------------------------------------------------------------


@app.command()
def snapshot(
    ctx: typer.Context,
    write: bool = typer.Option(False, "--write", help="Write .verity/snapshot.md."),
    compact: bool = typer.Option(False, "--compact", help="Token-efficient agent context."),
    out: Path | None = typer.Option(None, "--out", help="Write to a specific path."),
    json_out: bool = typer.Option(False, "--json", help="Agent context as JSON."),
) -> None:
    """Generate the reality snapshot: verified facts, assumptions, stale evidence."""
    engine = _engine(ctx)
    if json_out:
        console.print_json(json.dumps(build_agent_context(engine), indent=2))
        return
    status = engine.status()
    text = build_snapshot(status, compact=compact)
    target = out or (snapshot_path(engine.root) if write else None)
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        console.print(f"[green]wrote[/green] {relpath(target, engine.root)}")
        if write and not compact:
            console.print("[bright_black]Tip: `verity snapshot --compact` produces the token-efficient agent version.[/bright_black]")
        return
    console.print(text)


@app.command()
def graph(
    ctx: typer.Context,
    format: str = typer.Option("text", "--format", "-f", help="text | json | dot"),
    out: Path | None = typer.Option(None, "--out"),
    history: bool = typer.Option(False, "--history", help="Include superseded evidence."),
) -> None:
    """Render the claim → evidence → artifact graph."""
    engine = _engine(ctx)
    g = build_graph(engine, include_history=history)
    fmt = format.lower()
    if fmt == "json":
        payload = json.dumps(g.to_dict(), indent=2)
    elif fmt == "dot":
        payload = to_dot(g)
    elif fmt == "text":
        payload = to_text(g)
    else:
        err_console.print(f"[red]Unknown format: {escape(format)}[/red]")
        raise typer.Exit(2)
    if out:
        out.write_text(payload, encoding="utf-8")
        console.print(f"[green]wrote[/green] {out}")
    else:
        console.print(payload)


@contract_app.command("init")
def contract_init(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", help="Overwrite the existing file."),
) -> None:
    """Create .verity/contracts.yaml from the template."""
    root = _require_initialized(ctx)
    path = scaffold_contracts(root, force=force)
    if force:
        console.print(f"[green]wrote[/green] {relpath(path, root)}")
    else:
        console.print(f"[bright_black]ready[/bright_black] {relpath(path, root)}")
    console.print("\nEdit the contracts, then run [bold]verity contract check[/bold].")


@contract_app.command("list")
def contract_list(ctx: typer.Context) -> None:
    """List defined evidence contracts and their status."""
    engine = _engine(ctx)
    contracts = load_contracts(engine.root)
    if not contracts.contracts:
        console.print("[bright_black]No contracts defined. Run `verity contract init`.[/bright_black]")
        return
    console.print(f"Task: [bold]{escape(contracts.task or '(unnamed)')}[/bold]\n")
    claims = {c.contract_id: c for c in engine.repo.claims() if c.contract_id}
    for contract in contracts.contracts:
        claim = claims.get(contract.id)
        status = claim.status if claim else "unverified"
        style = STATUS_STYLE.get(status, "white")
        console.print(f"[{style}]{status.replace('_', ' ').upper():12s}[/{style}] [cyan]{escape(contract.id)}[/cyan]  {escape(contract.claim)}")
        console.print(f"             [bright_black]verifier={contract.verifier} required={contract.required}[/bright_black]")


@contract_app.command("check")
def contract_check(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Verify every contract and report what still blocks completion."""
    engine = _engine(ctx)
    contracts = load_contracts(engine.root)
    if not contracts.contracts:
        err_console.print(
            "[red]No evidence contracts defined.[/red] Run [bold]verity contract init[/bold] first."
        )
        raise typer.Exit(2)
    results = check_contracts(engine, contracts)
    if json_out:
        console.print_json(
            json.dumps(
                {
                    "task": contracts.task,
                    "results": [
                        {
                            "id": r.contract.id,
                            "claim": r.contract.claim,
                            "status": r.status,
                            "met": r.met,
                            "evidence": r.evidence_summary,
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
        )
    else:
        _print_header("VERITY contract check")
        console.print()
        console.print(f"Task: [bold]{escape(contracts.task or '(unnamed)')}[/bold]\n")
        for result in results:
            style = STATUS_STYLE.get(result.status, "white")
            mark = "PASS" if result.met else ("FAIL" if result.status == "contradicted" else "OPEN")
            mstyle = "green" if result.met else ("red" if result.status == "contradicted" else "yellow")
            console.print(f"[{mstyle}][{mark}][/{mstyle}] [cyan]{escape(result.contract.id)}[/cyan]  {escape(result.contract.claim)}")
            if result.evidence_summary:
                console.print(f"       [{style}]{escape(result.evidence_summary)}[/{style}]")
        unmet = [r for r in results if not r.met]
        console.print()
        style = "green" if not unmet else "yellow"
        console.print(
            f"[{style} bold]{len(results) - len(unmet)}/{len(results)} required contracts satisfied.[/{style} bold]"
        )
        if unmet:
            console.print("[bright_black]Completion is not justified until every required contract has current evidence.[/bright_black]")

    failed = any(
        (not r.met) and (r.contract.required or r.status == "contradicted") for r in results
    )
    raise typer.Exit(1 if failed else 0)


# ---------------------------------------------------------------------------
# guard / run / scan
# ---------------------------------------------------------------------------


def _render_decision(decision: Any) -> None:
    _print_header("VERITY guard")
    console.print()
    console.print(f"Action:  [bold]{escape(decision.action)}[/bold]")
    style = {"allow": "green", "warn": "yellow", "block": "red"}[decision.verdict.value]
    console.print(f"Verdict: [{style} bold]{decision.verdict.value.upper()}[/{style} bold]  risk: {decision.risk.value}")
    if decision.checks:
        console.print()
        for check in decision.checks:
            if check.satisfied:
                console.print(f"  [green][OK]     [/green] {escape(check.description)} [bright_black]{escape(check.evidence)}[/bright_black]")
            else:
                console.print(f"  [red][MISSING][/red] {escape(check.description)}")
                console.print(f"            [bright_black]{escape(check.evidence)}[/bright_black]")
                if check.hint:
                    console.print(f"            [yellow]→ {escape(check.hint)}[/yellow]")
    elif decision.reasons:
        console.print()
        for reason in decision.reasons:
            console.print(f"  [bright_black]{escape(reason)}[/bright_black]")
    if decision.verdict.value == "block":
        console.print()
        console.print("[red]The action relies on a prerequisite that does not exist. Fix the assumption, then retry.[/red]")
    console.print()


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def guard(
    ctx: typer.Context,
    command: list[str] | None = typer.Argument(None, help="Command to check."),
    strict: bool = typer.Option(False, "--strict", help="Block on any unmet prerequisite."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Check an action's assumptions before executing it (the hallucination firewall)."""
    engine = _engine(ctx)
    action = " ".join(command or ctx.args or [])
    if not action:
        err_console.print("[red]No command provided.[/red] Example: verity guard \"docker compose up --build\"")
        raise typer.Exit(2)
    decision = check_action(engine, action, strict=strict or None)
    if json_out:
        console.print_json(json.dumps(decision.to_dict(), indent=2))
    else:
        _render_decision(decision)
    raise typer.Exit({"allow": 0, "warn": 1, "block": 2}[decision.verdict.value])


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    ctx: typer.Context,
    command: list[str] | None = typer.Argument(None, help="Command to check and execute."),
    strict: bool = typer.Option(False, "--strict"),
) -> None:
    """Guard then execute a command with inherited stdio."""
    engine = _engine(ctx)
    action = " ".join(command or ctx.args or [])
    if not action:
        err_console.print("[red]No command provided.[/red]")
        raise typer.Exit(2)
    decision = check_action(engine, action, strict=strict or None)
    _render_decision(decision)
    if decision.verdict.value == "block":
        raise typer.Exit(2)
    engine.repo.record_event("action_executed", decision.to_dict())
    import subprocess

    proc = subprocess.run(action, cwd=str(engine.root), shell=True)
    raise typer.Exit(proc.returncode)


@app.command()
def scan(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Find unsupported assumptions in the working tree (imports, config)."""
    engine = _engine(ctx)
    findings = scan_assumptions(engine)
    if json_out:
        console.print_json(json.dumps([f.to_dict() for f in findings], indent=2))
        return
    _print_header("VERITY scan")
    console.print()
    if not findings:
        console.print("[green]No unsupported assumptions detected.[/green]")
        return
    for finding in findings:
        style = {"low": "bright_black", "medium": "yellow", "high": "red", "critical": "red"}[finding.severity.value]
        console.print(f"[{style}][{finding.severity.value.upper()}][/{style}] {escape(finding.description)}")
        console.print(f"       [bright_black]{escape(finding.evidence)}[/bright_black]")
        if finding.hint:
            console.print(f"       [yellow]→ {escape(finding.hint)}[/yellow]")
    console.print()
    if any(f.severity in {Risk.HIGH, Risk.CRITICAL} for f in findings):
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# integrations
# ---------------------------------------------------------------------------


@app.command()
def integrate(
    ctx: typer.Context,
    target: str | None = typer.Argument(
        None, help="Agent to configure: claude, codex, opencode, cursor, vscode, windsurf, gemini, generic."
    ),
    list_targets: bool = typer.Option(False, "--list", help="Show supported agents and exit."),
    all_targets: bool = typer.Option(False, "--all", help="Configure every supported agent."),
    print_only: bool = typer.Option(False, "--print", help="Print the MCP config instead of writing it."),
    use_project: bool = typer.Option(False, "--project", help="Write project-scoped config."),
    use_global: bool = typer.Option(False, "--global", help="Write user-scoped config."),
    with_instructions: bool = typer.Option(
        True, "--instructions/--no-instructions", help="Also write the agent instruction block."
    ),
    with_hooks: bool = typer.Option(
        False, "--hooks", help="Install an automatic PreToolUse guard (Claude Code only)."
    ),
    command: str = typer.Option("verity", "--command", help="Executable that launches VERITY."),
    use_python: bool = typer.Option(
        False, "--python", help="Launch with the current Python: `python -m verity mcp`."
    ),
    force: bool = typer.Option(False, "--force", help="Replace invalid or unmanaged config."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable result."),
) -> None:
    """Connect VERITY to an AI coding agent over MCP.

    Writes the agent's MCP config (merging, never clobbering) and adds a short
    evidence protocol to its instruction file. Idempotent: safe to re-run.
    """
    if list_targets or (target is None and not all_targets):
        table = Table(box=None, pad_edge=False, header_style="bold bright_black")
        table.add_column("Agent", style="cyan")
        table.add_column("Scope")
        table.add_column("Writes")
        table.add_column("Description")
        for item in TARGETS:
            files = []
            if item.project_path:
                files.append(item.project_path)
            if item.user_path and item.user_path != item.project_path:
                files.append("~/" + item.user_path)
            table.add_row(
                item.id,
                item.default_scope,
                "\n".join(files) or "stdout",
                item.description,
            )
        console.print(table)
        console.print(
            "[bright_black]Usage:[/bright_black] verity integrate claude  "
            "[bright_black]or[/bright_black] verity integrate --all"
        )
        return

    if use_project and use_global:
        err_console.print("[red]Choose either --project or --global, not both.[/red]")
        raise typer.Exit(2)
    if with_hooks and not all_targets and target != "claude":
        err_console.print(
            "[red]Automatic hooks are currently available for Claude Code only.[/red]"
        )
        raise typer.Exit(2)

    root = _require_initialized(ctx)
    scope = "project" if use_project else "user" if use_global else None
    selected = target_ids()[:-1] if all_targets else [target or ""]
    if target and target not in target_ids():
        err_console.print(
            f"[red]Unknown agent `{escape(target)}`.[/red] "
            f"Choose: {', '.join(target_ids())}."
        )
        raise typer.Exit(2)

    results = []
    failures = 0
    for item in selected:
        try:
            results.append(
                install(
                    item,
                    root,
                    scope=scope,
                    command=command,
                    use_python=use_python,
                    with_instructions=with_instructions,
                    with_hooks=with_hooks,
                    force=force,
                    print_only=print_only,
                )
            )
        except IntegrationError as exc:
            failures += 1
            err_console.print(f"[red]{escape(item)}:[/red] {escape(str(exc))}")

    if print_only:
        for result in results:
            for change in result.changes:
                if change.detail:
                    typer.echo(change.detail)
        raise typer.Exit(1 if failures else 0)

    if json_out:
        typer.echo(json.dumps([result.to_dict() for result in results], indent=2))
    else:
        table = Table(box=None, pad_edge=False, header_style="bold bright_black")
        table.add_column("Agent", style="cyan")
        table.add_column("Scope")
        table.add_column("File")
        table.add_column("Status")
        for result in results:
            for change in result.changes:
                label = (
                    result.target
                    if change.kind == "config"
                    else f"{result.target} ({change.kind})"
                )
                table.add_row(
                    label,
                    result.scope,
                    str(change.path) if change.path else "stdout",
                    change.status,
                )
        console.print(table)
        if results:
            console.print(
                "[bright_black]Restart the agent to load the MCP server. "
                "Verify with:[/bright_black] verity doctor"
            )

    raise typer.Exit(1 if failures else 0)


@app.command()
def mcp(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root", help="Project root for the MCP server."),
) -> None:
    """Run the VERITY MCP server over stdio (for coding agents)."""
    target = Path(root).resolve() if root else _root(ctx)
    if not verity_dir(target).is_dir():
        err_console.print(f"[red]VERITY is not initialized in {escape(str(target))}.[/red]")
        raise typer.Exit(2)
    from .server.mcp import run_stdio_server

    raise typer.Exit(run_stdio_server(target))


@app.command()
def serve(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8787, "--port"),
) -> None:
    """Serve the local API and dashboard."""
    root = _require_initialized(ctx)
    from .server.http import serve as serve_http

    serve_http(root, host=host, port=port)


@app.command()
def hook(
    ctx: typer.Context,
    action: str = typer.Argument("install", help="install | guard"),
    root: Path | None = typer.Option(None, "--root", help="Project root for guard."),
    command_text: str | None = typer.Option(
        None, "--command", help="Command to check; otherwise hook JSON is read from stdin."
    ),
    json_out: bool = typer.Option(False, "--json", help="Print the full decision."),
) -> None:
    """Install a git pre-commit hook, or run the agent PreToolUse guard.

    `verity hook guard` reads the JSON that Claude Code sends to a PreToolUse
    hook, checks the command against project reality, and returns a deny
    decision when a prerequisite is demonstrably missing.
    """
    if action == "guard":
        _hook_guard(ctx, root, command_text, json_out)
        return
    root_path = _require_initialized(ctx)
    if action != "install":
        err_console.print(f"[red]Unknown hook action: {escape(action)}[/red]")
        raise typer.Exit(2)
    if not is_git_repo(root_path):
        err_console.print("[red]Not a git repository.[/red]")
        raise typer.Exit(2)
    path = install_pre_commit_hook(root_path)
    console.print(f"[green]installed[/green] {relpath(path, root_path)}")
    console.print("[bright_black]Commits are blocked when a claim is contradicted.[/bright_black]")


def _hook_guard(
    ctx: typer.Context,
    root: Path | None,
    command_text: str | None,
    json_out: bool,
) -> None:
    raw = ""
    if not sys.stdin.isatty():
        raw = sys.stdin.read()
    payload: dict[str, Any] = {}
    if raw.strip():
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                payload = loaded
        except ValueError:
            payload = {}

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    command = (
        command_text
        or tool_input.get("command")
        or tool_input.get("cmd")
        or payload.get("command")
    )
    if not isinstance(command, str) or not command.strip():
        raise typer.Exit(0)

    if root is not None:
        target = Path(root).resolve()
    elif os.environ.get("CLAUDE_PROJECT_DIR"):
        target = Path(os.environ["CLAUDE_PROJECT_DIR"]).resolve()
    else:
        target = _root(ctx)
    if not verity_dir(target).is_dir():
        # An uninitialized project must not break the agent's shell.
        raise typer.Exit(0)

    engine = Engine(target)
    try:
        decision = check_action(engine, command)
    finally:
        engine.repo.close()

    if json_out:
        typer.echo(json.dumps(decision.to_dict(), indent=2))
        raise typer.Exit(2 if decision.verdict == Verdict.BLOCK else 0)

    if decision.verdict == Verdict.BLOCK:
        reason = (
            "VERITY blocked this command because a prerequisite is demonstrably "
            "missing. Fix the prerequisite or run `verity guard \"<command>\"` "
            "for details.\n" + "\n".join(f"- {r}" for r in decision.reasons)
        )
        typer.echo(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
    elif decision.verdict == Verdict.WARN:
        typer.echo(
            json.dumps(
                {
                    "systemMessage": "VERITY warning: " + " ".join(decision.reasons),
                }
            )
        )
    raise typer.Exit(0)


@app.command()
def validate(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Validate project configuration and evidence contracts."""
    root = _root(ctx)
    issues = validate_config(root) + validate_contracts(root)
    payload = {
        "valid": not issues,
        "issues": [issue.to_dict() for issue in issues],
    }
    if json_out:
        console.print_json(json.dumps(payload, indent=2))
    elif issues:
        err_console.print("[red]VERITY configuration needs attention:[/red]")
        for issue in issues:
            err_console.print(f"  [yellow]{escape(issue.path)}[/yellow]: {escape(issue.message)}")
    else:
        console.print("[green]Configuration and contracts are valid.[/green]")
    raise typer.Exit(0 if not issues else 1)


@app.command()
def doctor(
    ctx: typer.Context,
    fix: bool = typer.Option(False, "--fix", help="Create missing default VERITY configuration."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Check the local VERITY installation."""
    root = _root(ctx)
    repaired: list[str] = []
    if fix and not verity_dir(root).is_dir():
        root.mkdir(parents=True, exist_ok=True)
        scaffold_config(root)
        scaffold_contracts(root)
        repaired.append("created .verity/config.yaml")
        repaired.append("created .verity/contracts.yaml")
        repaired.append("created local evidence database")

    initialized = verity_dir(root).is_dir()
    payload: dict[str, Any] = {
        "version": __version__,
        "project_root": str(root),
        "initialized": initialized,
        "git_repository": is_git_repo(root),
        "repaired": repaired,
    }
    if initialized:
        cfg = Config.load(root)
        engine = Engine(root, config=cfg)
        payload.update(
            {
                "database": str(engine.repo.path),
                "project": engine.repo.project,
                "claims": len(engine.repo.claims()),
                "execution_allowed": cfg.verification.allow_exec,
                "execution_mode": "allowlist only" if not cfg.verification.allow_exec else "enabled",
            }
        )
        issues = validate_config(root) + validate_contracts(root)
        payload["validation"] = {
            "valid": not issues,
            "issues": [issue.to_dict() for issue in issues],
        }
        engine.repo.close()

    if json_out:
        console.print_json(json.dumps(payload, indent=2))
        return

    table = Table(box=None, pad_edge=False, header_style="bold bright_black")
    table.add_column("Check", style="cyan")
    table.add_column("Result")
    table.add_row("VERITY", __version__)
    table.add_row("Project root", str(root))
    table.add_row("Initialized", "yes" if initialized else "no")
    table.add_row("Git repository", "yes" if payload["git_repository"] else "no")
    if initialized:
        table.add_row("Database", str(payload["database"]))
        table.add_row("Project", str(payload["project"]))
        table.add_row("Claims", str(payload["claims"]))
        table.add_row("Execution", str(payload["execution_mode"]))
        if payload["validation"]["valid"]:
            table.add_row("Configuration", "[green]valid[/green]")
        else:
            table.add_row("Configuration", "[red]issues found[/red]")
    for item in repaired:
        table.add_row("Repaired", item)
    console.print(table)


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        err_console.print("\n[bright_black]Interrupted.[/bright_black]")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
