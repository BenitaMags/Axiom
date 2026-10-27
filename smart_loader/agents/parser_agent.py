"""
agents/parser_agent.py
─────────────────────
Parser Agent — Node 1 in the LangGraph pipeline.

Reads the target Python file, walks its AST, and extracts every import
statement into a structured list of ImportInfo objects.

Also discovers which API methods from each package are actually called
in the source code — giving the Resolver Agent richer context.

No LLM call here — pure deterministic static analysis.
Mirrors what an OS loader does reading an ELF header and its .dynamic section.
"""

from __future__ import annotations
import ast
import os
from pathlib import Path
from rich.console import Console
from rich.table import Table
from langchain_core.messages import AIMessage

from smart_loader.core.state import ImportInfo, AgentEvent, LoaderState

console = Console()


# ── AST Visitors ──────────────────────────────────────────────────────────────

class ImportVisitor(ast.NodeVisitor):
    """Collect every import statement from the AST."""

    def __init__(self):
        self.imports: list[ImportInfo] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append(ImportInfo(
                module=alias.name,
                alias=alias.asname,
                names=[],
                line=node.lineno,
            ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        names  = [a.name for a in node.names if a.name != "*"]
        self.imports.append(ImportInfo(
            module=module,
            alias=None,
            names=names,
            line=node.lineno,
        ))
        self.generic_visit(node)


class CallSiteVisitor(ast.NodeVisitor):
    """
    Collect API usage in two forms:
      1. Attribute access: requests.get(...) -> {requests: {get}}
      2. Direct calls to a from-imported name: KafkaProducer(...) after
         `from kafka import KafkaProducer` -> {kafka: {KafkaProducer}}

    Form 2 was previously MISSING entirely — this visitor only ever caught
    `module.attr(...)` call sites. That's a real gap: `from X import Name;
    Name(...)` is at least as common a call style as `X.attr(...)`, and
    without it, `imp.api_calls` comes back empty for files using it, which
    cascades into axiom_agent's `_score_api_compat` short-circuiting to
    identical neutral scores for every candidate (its `if not used_apis`
    fallback) — every equivalence group then ties, and since Python's max()
    keeps the FIRST equally-scored candidate, the tie always resolves to
    the ORIGINAL package (always listed first), even when a strictly better
    replacement exists. `name_to_key` (built by the caller from the from-
    import names actually collected — see parser_agent) is what lets this
    visitor attribute a bare `Name(...)` call back to the right package.
    """

    def __init__(self, name_to_key: dict[str, str] | None = None):
        self.calls: dict[str, set[str]] = {}
        self.name_to_key = name_to_key or {}

    def visit_Attribute(self, node: ast.Attribute):
        if isinstance(node.value, ast.Name):
            # ``from Crypto.Cipher import AES; AES.new(...)`` is still a
            # call on the imported package, not on an unrelated local name.
            # Resolve the base through the same import binding map used for
            # direct calls so downstream Resolver/Profiler evidence records
            # the real ``new`` API rather than silently dropping it.
            key = self.name_to_key.get(node.value.id, node.value.id)
            self.calls.setdefault(key, set()).add(node.attr)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in self.name_to_key:
            key = self.name_to_key[node.func.id]
            self.calls.setdefault(key, set()).add(node.func.id)
        self.generic_visit(node)


# ── Agent ──────────────────────────────────────────────────────────────────────

def parser_agent(state: LoaderState) -> dict:
    source_file = state.get("source_file", "")
    console.rule("[bold cyan]🔍 Parser Agent")

    trace = list(state.get("agent_trace", []))
    trace.append(AgentEvent(stage="parser", event="started", detail={"file": source_file}))

    # ── Read file ──────────────────────────────────────────────────────────────
    # source_file is normally already absolute by the time it reaches here
    # (cli.py resolves it via Path(file).resolve() before calling run_loader),
    # but resolve defensively against any caller that passes a relative path
    # by anchoring to the current working directory rather than relying on
    # Path()'s default behavior, which resolves relative to wherever the
    # process happens to be — a real problem for axiom installed as a global
    # CLI tool, where cwd can be anywhere.
    path = Path(source_file)
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()

    console.print(f"[dim]Resolved path: {path}[/dim]")

    if not path.exists():
        return {"error": f"File not found: {source_file} (resolved to {path})", "done": True, "agent_trace": trace}

    if not path.is_file():
        return {"error": f"Not a file (is directory): {path}", "done": True, "agent_trace": trace}

    source_code = path.read_text(encoding="utf-8")

    # ── Parse AST ─────────────────────────────────────────────────────────────
    try:
        tree = ast.parse(source_code, filename=str(path))
    except SyntaxError as e:
        return {"error": f"Syntax error: {e}", "done": True, "agent_trace": trace}

    # ── Extract imports ────────────────────────────────────────────────────────
    iv = ImportVisitor()
    iv.visit(tree)
    imports = iv.imports

    # ── Extract call sites ─────────────────────────────────────────────────────
    # name_to_key maps each bare name pulled in via `from X import Name` back
    # to the merge-key `imp.api_calls` is later looked up by (alias, or the
    # module's top-level segment) — see CallSiteVisitor's docstring for why
    # this is needed to catch `Name(...)` call sites, not just `X.attr(...)`.
    name_to_key: dict[str, str] = {}
    for imp in imports:
        if not imp.names:
            continue
        key = imp.alias if imp.alias else imp.module.split(".")[0]
        for name in imp.names:
            name_to_key[name] = key

    cv = CallSiteVisitor(name_to_key=name_to_key)
    cv.visit(tree)

    # Build alias → module map, then annotate each ImportInfo with api_calls
    alias_map: dict[str, str] = {}
    for imp in imports:
        key = imp.alias if imp.alias else imp.module.split(".")[0]
        alias_map[key] = imp.module

    for imp in imports:
        key = imp.alias if imp.alias else imp.module.split(".")[0]
        imp.api_calls = list(cv.calls.get(key, set()))

    # ── Print results table ────────────────────────────────────────────────────
    table = Table(title="Discovered Imports", style="cyan", header_style="bold magenta")
    table.add_column("Line",  justify="right")
    table.add_column("Module")
    table.add_column("Alias")
    table.add_column("Names")
    table.add_column("API Calls Used")

    for imp in imports:
        table.add_row(
            str(imp.line),
            imp.module,
            imp.alias or "—",
            ", ".join(imp.names) or "—",
            ", ".join(imp.api_calls) or "—",
        )
    console.print(table)
    console.print(f"\n[green]✓ Found {len(imports)} imports[/green]\n")

    trace.append(AgentEvent(
        stage="parser",
        event="completed",
        detail={"imports_found": len(imports), "modules": [i.module for i in imports]},
    ))

    return {
        "source_code":  source_code,
        "imports":      imports,
        "agent_trace":  trace,
        "messages":     [AIMessage(content=f"Parser: found {len(imports)} imports in {source_file}")],
        "error":        None,
    }
