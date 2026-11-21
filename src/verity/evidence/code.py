"""Static-analysis verifiers: symbols, routes, syntax, imports."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..models import Claim, EvidenceResult
from ..util import CODE_EXTS, load_json, load_toml, read_text, relpath, run_command, walk_files
from .base import VerifyContext, fail, ok, warn

STDLIB = set(getattr(sys, "stdlib_module_names", set()))


def _candidate_files(ctx: VerifyContext, path: str | None = None, exts: set[str] | None = None) -> list[Path]:
    if path:
        p = (ctx.root / path).resolve()
        try:
            p.relative_to(ctx.root.resolve())
        except ValueError:
            return []
        return [p] if p.is_file() else []
    return walk_files(ctx.root, exts=exts or CODE_EXTS, limit=4000)


def _symbol_patterns(symbol: str, suffix: str) -> list[re.Pattern[str]]:
    s = re.escape(symbol)
    common = [
        re.compile(rf"\b(?:def|function|class|interface|struct|enum|trait|type|fn|object)\s+{s}\b"),
        re.compile(rf"\b(?:const|let|var|static|final|public|private|protected)\s+[\w<>\[\], ]*\b{s}\s*[=;({{]"),
    ]
    if suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
        return common + [
            re.compile(rf"\b{s}\s*[:=]\s*(?:async\s*)?(?:function|\()"),
            re.compile(rf"\b{s}\s*\([^)]*\)\s*\{{"),
        ]
    if suffix in {".java", ".kt", ".kts", ".scala", ".cs"}:
        return common + [
            re.compile(rf"\b{s}\s*\([^)]*\)\s*(?:throws\s+[\w, .]+)?\{{"),
        ]
    if suffix == ".go":
        return [
            re.compile(rf"\bfunc\s+(?:\([^)]*\)\s*)?{s}\s*\("),
            re.compile(rf"\btype\s+{s}\b"),
            re.compile(rf"\b{s}\s*:?="),
        ]
    if suffix == ".rs":
        return [
            re.compile(rf"\bfn\s+{s}\b"),
            re.compile(rf"\b(?:struct|enum|trait|type|const|static)\s+{s}\b"),
        ]
    return common + [re.compile(rf"\b{s}\s*\(")]


@dataclass
class SymbolHit:
    path: str
    line: int
    name: str
    qualified: str


class _PySymbolVisitor(ast.NodeVisitor):
    def __init__(self, path: str):
        self.path = path
        self.hits: list[SymbolHit] = []
        self.stack: list[str] = []

    def _visit_def(self, node: ast.AST) -> None:
        name = getattr(node, "name", "")
        qualified = ".".join([*self.stack, name]) if self.stack else name
        self.hits.append(SymbolHit(self.path, getattr(node, "lineno", 0), name, qualified))
        self.stack.append(name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_def(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_def(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._visit_def(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for target in node.targets:
            if isinstance(target, ast.Name):
                qualified = ".".join([*self.stack, target.id]) if self.stack else target.id
                self.hits.append(SymbolHit(self.path, node.lineno, target.id, qualified))
        self.generic_visit(node)


class SymbolVerifier:
    name = "code_symbol"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        raw = str(claim.args.get("symbol") or "").strip().strip("`'\"")
        raw = re.sub(r"\(\s*\)$", "", raw)
        if not raw:
            return fail("No symbol was specified for this claim", details="claim.args.symbol is empty")
        symbol = raw.split(".")[-1] if "." in raw else raw
        files = _candidate_files(ctx, claim.args.get("path"))
        if not files:
            return fail(
                f"Symbol `{raw}` cannot exist: no source files were found",
                details=f"path={claim.args.get('path')!r}",
            )

        hits: list[SymbolHit] = []
        mentions: list[str] = []
        for path in files:
            suffix = path.suffix.lower()
            if suffix == ".py":
                try:
                    tree = ast.parse(read_text(path))
                except (SyntaxError, ValueError):
                    continue
                visitor = _PySymbolVisitor(relpath(path, ctx.root))
                visitor.visit(tree)
                hits.extend(
                    h for h in visitor.hits if h.name == symbol or h.qualified == raw
                )
                continue
            text = read_text(path)
            if not text:
                continue
            file_hits = 0
            for pattern in _symbol_patterns(symbol, suffix):
                match = pattern.search(text)
                if match:
                    line = text[: match.start()].count("\n") + 1
                    hits.append(
                        SymbolHit(relpath(path, ctx.root), line, symbol, symbol)
                    )
                    file_hits += 1
                    break
            if not file_hits and re.search(rf"\b{re.escape(symbol)}\b", text):
                mentions.append(relpath(path, ctx.root))

        if hits:
            locations = ", ".join(f"{h.path}:{h.line}" for h in hits[:6])
            deps = sorted({h.path for h in hits})
            return ok(
                f"`{raw}` is defined in {len({h.path for h in hits})} file(s)",
                details=f"Definition found at {locations}",
                payload={"locations": [f"{h.path}:{h.line}" for h in hits[:50]], "symbol": raw},
                deps=deps,
                confidence=1.0,
            )
        if mentions:
            return warn(
                f"`{raw}` appears as an identifier but no definition was found",
                details="Mentioned in: " + ", ".join(sorted(set(mentions))[:8]),
                payload={"mentions": sorted(set(mentions))[:50], "symbol": raw},
                deps=sorted(set(mentions)),
                confidence=0.4,
            )
        return fail(
            f"Symbol `{raw}` was not found in the codebase",
            details=f"Searched {len(files)} source file(s).",
            payload={"symbol": raw, "files_searched": len(files)},
            confidence=0.95,
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_ROUTE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Python decorators / JS method calls
    (
        re.compile(r"@\s*[\w.]+\.(get|post|put|patch|delete|route|api_route)\(\s*[\"']([^\"']+)[\"']", re.I),
        "decorator",
    ),
    (
        re.compile(r"\b(?:app|router|server|api|blueprint|bp)\.(get|post|put|patch|delete|all)\(\s*[\"']([^\"']+)[\"']", re.I),
        "method",
    ),
    # Spring / JAX-RS
    (
        re.compile(r"@(Get|Post|Put|Patch|Delete)Mapping\(\s*(?:value\s*=\s*)?[\"']([^\"']+)[\"']", re.I),
        "spring",
    ),
    (
        re.compile(r"@(?:Get|Post|Put|Patch|Delete)Mapping\(\s*path\s*=\s*[\"']([^\"']+)[\"']", re.I),
        "spring-path",
    ),
    (
        re.compile(r"@RequestMapping\(\s*(?:value\s*=\s*)?[\"']([^\"']+)[\"'][^)]*?method\s*=\s*RequestMethod\.(GET|POST|PUT|PATCH|DELETE)", re.I | re.S),
        "spring-req",
    ),
    (
        re.compile(r"@Path\(\s*[\"']([^\"']+)[\"']"),
        "jaxrs",
    ),
    # Django / Flask
    (
        re.compile(r"\b(?:re_)?path\(\s*[\"']([^\"']+)[\"']"),
        "django",
    ),
    # Rails routes.rb
    (
        re.compile(r"^\s*(get|post|put|patch|delete)\s+[\"']([^\"']+)[\"']", re.M),
        "rails",
    ),
]


def normalize_route(path: str) -> str:
    path = (path or "").split("?")[0].strip().strip("`'\"")
    if not path:
        return "/"
    if not path.startswith("/"):
        path = "/" + path
    path = re.sub(r"\{[^}]+\}|<[^>]+>|:[A-Za-z_]\w*|\$\{[^}]+\}", "*", path)
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def _route_matches(claim_path: str, code_path: str, fuzzy: bool) -> bool:
    a, b = normalize_route(claim_path), normalize_route(code_path)
    if a == b:
        return True
    if not fuzzy:
        return False
    a_parts = [p for p in a.split("/") if p]
    b_parts = [p for p in b.split("/") if p]
    if a_parts and b_parts and a_parts[-1] == b_parts[-1]:
        return True
    return b.endswith(a) or a.endswith(b)


@dataclass
class RouteHit:
    method: str | None
    path: str
    location: str


def _collect_routes(ctx: VerifyContext) -> list[RouteHit]:
    routes: list[RouteHit] = []
    for path in _candidate_files(ctx):
        text = read_text(path)
        if not text:
            continue
        rel = relpath(path, ctx.root)
        for pattern, kind in _ROUTE_PATTERNS:
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                groups = match.groups()
                if kind in {"decorator", "method", "spring"}:
                    method, route_path = groups[0].upper(), groups[1]
                    if method == "ROUTE" or method == "API_ROUTE" or method == "ALL":
                        method = None
                elif kind == "spring-path":
                    method, route_path = None, groups[0]
                elif kind == "spring-req":
                    route_path, method = groups[0], groups[1].upper()
                elif kind == "jaxrs":
                    method, route_path = None, groups[0]
                elif kind == "django":
                    method, route_path = None, groups[0]
                elif kind == "rails":
                    method, route_path = groups[0].upper(), groups[1]
                else:
                    continue
                routes.append(RouteHit(method, route_path, f"{rel}:{line}"))
        # Flask @app.route(..., methods=[...]) needs the methods list parsed.
        for match in re.finditer(
            r"@\s*[\w.]+\.route\(\s*[\"']([^\"']+)[\"']([^)]*)\)", text, re.S
        ):
            methods = re.findall(r"[\"']([A-Z]+)[\"']", match.group(2) or "")
            line = text[: match.start()].count("\n") + 1
            for method in methods or ["GET"]:
                routes.append(RouteHit(method, match.group(1), f"{rel}:{line}"))
    return routes


class RouteVerifier:
    name = "code_route"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        path = str(claim.args.get("path") or "").strip()
        method = (claim.args.get("method") or "").upper() or None
        fuzzy = bool(claim.args.get("fuzzy"))
        if not path:
            return fail("No route path was specified for this claim")
        routes = _collect_routes(ctx)
        matches = [r for r in routes if _route_matches(path, r.path, fuzzy)]
        if not matches:
            return fail(
                f"Route `{path}` was not found in the codebase",
                details=f"Scanned {len(routes)} route declaration(s).",
                payload={"path": path, "method": method, "routes_found": len(routes)},
                confidence=0.9,
            )
        if method:
            exact = [r for r in matches if r.method == method]
            if exact:
                return ok(
                    f"Route {method} {path} is declared",
                    details="Declared at " + ", ".join(r.location for r in exact[:6]),
                    payload={
                        "path": path,
                        "method": method,
                        "locations": [r.location for r in exact[:20]],
                    },
                    deps=sorted({r.location.split(":")[0] for r in exact}),
                )
            methods = sorted({r.method or "ANY" for r in matches})
            return warn(
                f"Route `{path}` exists but not with method {method}",
                details=f"Found methods: {', '.join(methods)}",
                payload={"path": path, "method": method, "found_methods": methods},
                deps=sorted({r.location.split(":")[0] for r in matches}),
                confidence=0.5,
            )
        return ok(
            f"Route `{path}` is declared",
            details="Declared at " + ", ".join(r.location for r in matches[:6]),
            payload={
                "path": path,
                "locations": [r.location for r in matches[:20]],
                "methods": sorted({r.method or "ANY" for r in matches}),
            },
            deps=sorted({r.location.split(":")[0] for r in matches}),
        )


# ---------------------------------------------------------------------------
# Syntax
# ---------------------------------------------------------------------------


class SyntaxVerifier:
    name = "syntax"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        paths = [str(p) for p in (claim.args.get("paths") or [])]
        if not paths and ctx.changes:
            paths = [c.path for c in ctx.changes.files if not c.binary]
        if not paths:
            return warn("No files were specified for syntax verification")
        failures: list[str] = []
        checked: list[str] = []
        deps: list[str] = []
        warnings: list[str] = []
        for rel in paths:
            path = (ctx.root / rel).resolve()
            try:
                path.relative_to(ctx.root.resolve())
            except ValueError:
                failures.append(f"{rel}: path escapes project root")
                continue
            if not path.is_file():
                failures.append(f"{rel}: file does not exist")
                continue
            checked.append(rel)
            deps.append(rel)
            suffix = path.suffix.lower()
            text = read_text(path)
            try:
                if suffix == ".py":
                    compile(text, str(path), "exec")
                elif suffix == ".json":
                    json.loads(text)
                elif suffix in {".yaml", ".yml"}:
                    import yaml

                    yaml.safe_load(text)
                elif suffix == ".toml":
                    try:
                        import tomllib

                        tomllib.loads(text)
                    except ImportError:
                        warnings.append(f"{rel}: tomllib unavailable; TOML not parsed")
                    except Exception as exc:
                        failures.append(f"{rel}: {exc}")
                elif suffix in {".js", ".mjs", ".cjs"} and shutil.which("node"):
                    result = run_command(["node", "--check", str(path)], ctx.root, timeout=20)
                    if not result.ok:
                        failures.append(f"{rel}: {result.tail(400)}")
                elif suffix in {".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs"}:
                    if not _balanced(text):
                        failures.append(f"{rel}: unbalanced braces/parentheses")
                    else:
                        warnings.append(f"{rel}: only structural checks were possible")
            except SyntaxError as exc:
                failures.append(f"{rel}:{exc.lineno}: {exc.msg}")
            except json.JSONDecodeError as exc:
                failures.append(f"{rel}:{exc.lineno}: {exc.msg}")
            except Exception as exc:  # yaml/toml parser errors
                failures.append(f"{rel}: {type(exc).__name__}: {exc}")

        if failures:
            return fail(
                f"{len(failures)} file(s) failed syntax validation",
                details="\n".join(failures[:20]),
                payload={"failures": failures[:50], "checked": checked},
                deps=deps,
            )
        if warnings and len(warnings) == len(checked):
            return warn(
                f"{len(checked)} file(s) passed only structural checks",
                details="\n".join(warnings[:10]),
                payload={"checked": checked},
                deps=deps,
                confidence=0.4,
            )
        return ok(
            f"{len(checked)} file(s) parse without syntax errors",
            details=", ".join(checked[:10]),
            payload={"checked": checked, "warnings": warnings[:20]},
            deps=deps,
        )


def _balanced(text: str) -> bool:
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    in_str: str | None = None
    escaped = False
    for ch in text:
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in "\"'`":
            in_str = ch
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in pairs.values():
            if not stack or stack.pop() != ch:
                return False
    return not stack and in_str is None


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

_JS_IMPORT = re.compile(
    r"(?:import\s+(?:[\w*{},\s]+\s+from\s+)?|require\(\s*)"
    r"[\"']([^\"']+)[\"']"
)


def _local_module_exists(root: Path, module: str) -> bool:
    candidates = [
        root / f"{module}.py",
        root / module / "__init__.py",
        root / "src" / f"{module}.py",
        root / "src" / module / "__init__.py",
    ]
    if any(p.is_file() for p in candidates):
        return True
    # namespace packages / monorepos
    for base in (root, root / "src"):
        try:
            for p in base.glob(f"*/{module}.py"):
                return True
        except OSError:
            pass
    return False


def _module_installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError, AttributeError):
        return False


class ImportsVerifier:
    name = "imports"

    def verify(self, claim: Claim, ctx: VerifyContext) -> EvidenceResult:
        paths = [str(p) for p in (claim.args.get("paths") or [])]
        if not paths and ctx.changes:
            paths = [c.path for c in ctx.changes.files if c.path.endswith((".py", ".js", ".ts", ".tsx", ".jsx"))]
        if not paths:
            return warn("No files were specified for import verification")

        missing: list[str] = []
        checked: list[str] = []
        external: set[str] = set()
        deps: list[str] = []
        for rel in paths:
            path = ctx.root / rel
            if not path.is_file():
                continue
            deps.append(rel)
            text = read_text(path)
            suffix = path.suffix.lower()
            if suffix == ".py":
                try:
                    tree = ast.parse(text)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            top = alias.name.split(".")[0]
                            checked.append(f"{rel}: {top}")
                            if top in STDLIB or _local_module_exists(ctx.root, top):
                                continue
                            external.add(top)
                            if not _module_installed(top):
                                missing.append(f"{rel}:{node.lineno}: module `{top}` is not installed")
                    elif isinstance(node, ast.ImportFrom):
                        if node.level:
                            continue
                        top = (node.module or "").split(".")[0]
                        if not top:
                            continue
                        checked.append(f"{rel}: {top}")
                        if top in STDLIB or _local_module_exists(ctx.root, top):
                            continue
                        external.add(top)
                        if not _module_installed(top):
                            missing.append(f"{rel}:{node.lineno}: module `{top}` is not installed")
            elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
                for match in _JS_IMPORT.finditer(text):
                    spec = match.group(1)
                    if spec.startswith((".", "/", "~")):
                        continue
                    pkg = spec if spec.startswith("@") else spec.split("/")[0]
                    if spec.startswith("@"):
                        parts = spec.split("/")
                        pkg = "/".join(parts[:2])
                    checked.append(f"{rel}: {pkg}")
                    external.add(pkg)
                    if not (ctx.root / "node_modules" / pkg).exists():
                        manifest = load_json(ctx.root / "package.json") or {}
                        declared = {
                            **(manifest.get("dependencies") or {}),
                            **(manifest.get("devDependencies") or {}),
                        }
                        if pkg not in declared:
                            missing.append(f"{rel}: package `{pkg}` is not installed or declared")

        if missing:
            return fail(
                f"{len(missing)} unresolved import(s) — the code assumes modules that are not available",
                details="\n".join(missing[:20]),
                payload={"missing": missing[:100], "external": sorted(external)},
                deps=deps,
                confidence=0.95,
            )
        return ok(
            f"All imports resolve ({len(checked)} checked)",
            details=", ".join(sorted(external))[:800] or "Only stdlib and local imports were used.",
            payload={"checked": checked[:200], "external": sorted(external)},
            deps=deps,
        )
