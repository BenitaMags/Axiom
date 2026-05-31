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
    Collect attribute access patterns: requests.get(...) → {requests: [get]}
    Used to tell the Resolver which specific APIs are actually used.
    """

    def __init__(self):
        self.calls: dict[str, set[str]] = {}

    def visit_Attribute(self, node: ast.Attribute):
        if isinstance(node.value, ast.Name):
            self.calls.setdefault(node.value.id, set()).add(node.attr)
        self.generic_visit(node)


# ── Agent ──────────────────────────────────────────────────────────────────────

def parser_agent(state: LoaderState) -> dict:
    source_file = state.get("source_file", "")
    console.rule("[bold cyan]🔍 Parser Agent")

    trace = list(state.get("agent_trace", []))
    trace.append(AgentEvent(stage="parser", event="started", detail={"file": source_file}))

    # ── Read file ──────────────────────────────────────────────────────────────
    '''
    path = Path(os.getcwd()) / source_file if not Path(source_file).is_absolute() else Path(source_file)
    path = path.resolve()

    if not path.exists():
        return {"error": f"File not found: {source_file}", "done": True, "agent_trace": trace}

    source_code = path.read_text(encoding="utf-8")
'''
    path = Path(source_file)
    
    # If relative, it's already absolute from cli.py, so just use it
    # If somehow it's relative, make it absolute explicitly
    if not path.is_absolute():
        path = Path.cwd() / path
    
    path = path.resolve()

    print(f"[DEBUG] source_file input = {source_file}")
    print(f"[DEBUG] resolved path = {path}")
    print(f"[DEBUG] exists? {path.exists()}")

    if not path.exists():
        print(f"[DEBUG] FILE NOT FOUND: {path}")  # ← ADD THIS
        return {"error": f"File not found: {source_file} (resolved to {path})", "done": True, "agent_trace": trace}
    
    if not path.is_file():
        print(f"[DEBUG] NOT A FILE: {path}")  # ← ADD THIS
        return {"error": f"Not a file (is directory): {path}", "done": True, "agent_trace": trace}

    source_code = path.read_text(encoding="utf-8")
    print(f"[DEBUG] Read {len(source_code)} bytes")  # ← ADD THIS


    # ── Parse AST ─────────────────────────────────────────────────────────────
    try:
        tree = ast.parse(source_code, filename=str(path))
        print(f"[DEBUG] AST parsed successfully")
    except SyntaxError as e:
        print(f"[DEBUG] SYNTAX ERROR: {e}")
        return {"error": f"Syntax error: {e}", "done": True, "agent_trace": trace}

    # ── Extract imports ────────────────────────────────────────────────────────
    iv = ImportVisitor()
    iv.visit(tree)
    imports = iv.imports

    print(f"[DEBUG] Found {len(imports)} imports: {[i.module for i in imports]}")
    # ── Extract call sites ─────────────────────────────────────────────────────
    cv = CallSiteVisitor()
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