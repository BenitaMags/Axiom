"""
agents/connector_agent.py
──────────────────────────
Connector Agent — Final node in the AXIOM LangGraph pipeline.

APPROACH (v3 — inline injection):
  Instead of generating a separate .py module and rewriting imports,
  this agent injects transformation helper functions DIRECTLY into the
  top of the target source file, right after the imports block.

  This means:
    - No separate connector file to manage or save
    - No import rewriting (the original package name stays)
    - The functions wrap incompatible API calls at the call site level
    - Every injected function has a comment block explaining what it does
      and WHY it's needed (the input/output type mismatch)

  Example — hashlib → xxhash:
    Original code:  hashlib.sha256(blob).hexdigest()
    Problem:        xxhash.sha256(blob) returns an xxhash object,
                    same .hexdigest() interface — but if the model
                    swaps to a package where return type differs,
                    we inject a wrapper.

DETECTION LOGIC (no hardcoding):
  The agent uses AST analysis to extract EVERY call site of the original
  package, including full method chains (e.g. .sha256().hexdigest()).
  It passes these to the LLM with a structured checklist so the LLM
  can reason about return type compatibility before generating adapters.

  The LLM is asked to:
    1. For each call site: does the new package return a compatible type?
    2. If yes → no wrapper needed, note it
    3. If no  → generate a named wrapper function with full docstring
    4. Then rewrite ONLY the affected call sites in the source

OUTPUT:
  - patched_code: original source with injected helpers + rewritten call sites
  - connectors: dict of {original: {"functions": [...], "call_rewrites": [...]}}
"""

from __future__ import annotations
import ast
import time
from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
from smart_loader.core.ast_rewrite import apply_import_replacements
from smart_loader.core.llm_factory import _get_llm
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

console = Console()



# ── AST call site extractor ───────────────────────────────────────────────────

def _extract_call_sites(source_code: str, package: str) -> list[dict]:
    """
    Walk the AST and extract every call site that involves `package`.
    Returns a list of dicts with:
      - line       : line number (1-based)
      - source_line: the full source line text
      - call_expr  : the call expression as a string (e.g. "hashlib.sha256(blob)")
      - full_expr  : the outermost expression (e.g. "hashlib.sha256(blob).hexdigest()")
      - method_chain: list of chained method names after the call (e.g. ["hexdigest"])
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return []

    lines = source_code.splitlines()
    results = []

    # First pass: find import alias for the package
    pkg_alias = package
    imported_names: dict[str, str] = {}  # local_name → original_name

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == package or alias.name.startswith(f"{package}."):
                    pkg_alias = alias.asname or alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == package or node.module.startswith(f"{package}.")):
                for alias in node.names:
                    imported_names[alias.asname or alias.name] = alias.name

    def _get_base_name(node) -> str | None:
        """Walk an Attribute/Name chain and return the root Name id."""
        while isinstance(node, ast.Attribute):
            node = node.value
        if isinstance(node, ast.Name):
            return node.id
        return None

    def _involves_pkg(node) -> bool:
        base = _get_base_name(node)
        return base == pkg_alias or base in imported_names

    def _collect_chain(node) -> list[str]:
        """Collect attribute chain upward from a node."""
        chain = []
        current = node
        while isinstance(current, ast.Attribute):
            chain.append(current.attr)
            current = current.value
        return list(reversed(chain))

    # Second pass: collect call nodes that reference the package
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _involves_pkg(node.func):
            continue

        call_str = ast.unparse(node)
        line_num  = node.lineno
        src_line  = lines[line_num - 1].strip() if line_num <= len(lines) else ""

        # Detect method chain AFTER this call
        # e.g. hashlib.sha256(blob).hexdigest() — the Call node for sha256()
        # will be the .value of an Attribute node (.hexdigest) which is
        # itself the .func of another Call node (.hexdigest())
        method_chain: list[str] = []

        results.append({
            "line":         line_num,
            "source_line":  src_line,
            "call_expr":    call_str,
            "full_expr":    src_line,   # full line gives best context
            "method_chain": method_chain,
        })

    return results


def _find_import_block_end(source_code: str) -> int:
    """
    Return the line index (0-based) AFTER the last import statement
    in the file. This is where we inject helper functions.
    """
    lines = source_code.splitlines()
    last_import_line = 0
    try:
        tree = ast.parse(source_code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                last_import_line = max(last_import_line, node.lineno)
    except SyntaxError:
        pass
    return last_import_line   # 1-based line number of last import


# ── Prompt ────────────────────────────────────────────────────────────────────

ANALYSIS_SYSTEM = """You are an expert Python API compatibility engineer.

You will be given:
1. A package migration: FROM package A → TO package B
2. Every call site in the source code where package A is used (with full context)
3. Known pitfalls about the migration

Your job is to:

STEP 1 — ANALYZE each call site:
  For each call, determine:
  a) Does package B have an equivalent function/method with the same name?
  b) Does the return type match? (CRITICAL — e.g. bytes vs str, object vs primitive)
  c) Does the method chain still work? (e.g. .sha256(data).hexdigest())
  d) Are parameter names/types compatible?

STEP 2 — GENERATE wrapper functions ONLY for incompatible call sites.
  Rules:
  - If the call is fully compatible → write "COMPATIBLE: no wrapper needed"
  - If incompatible → write a Python wrapper function that:
    * Has the EXACT same signature as the original call pattern
    * Converts inputs/outputs silently
    * Is named descriptively: e.g. `_axiom_sha256_wrapper`
    * Has a detailed docstring explaining:
      - What the original package returned
      - What the new package returns  
      - What transformation is applied and why

Return a JSON object with this exact structure:
{
  "analysis": [
    {
      "line": <int>,
      "call": "<call expression>",
      "compatible": <true/false>,
      "reason": "<why compatible or not>",
      "wrapper_name": "<function name if incompatible, else null>"
    }
  ],
  "wrappers": [
    {
      "name": "<function name>",
      "code": "<complete Python function definition with docstring>",
      "replaces": "<original call pattern>",
      "with": "<new call pattern using wrapper>"
    }
  ]
}

CRITICAL RULES:
- If NO wrappers are needed, set wrappers to []
- The 'replaces' field must be the EXACT call expression found in the source code that needs replacing.
"""

ANALYSIS_HUMAN = """
MIGRATION: {original} → {winner}
ROLE: {role}

KNOWN PITFALLS:
{pitfalls}

CALL SITES FOUND IN SOURCE (all usages of {original}):
{call_sites}

FULL SOURCE CODE:
```python
{source_code}
```

Analyze every call site, generate wrappers where needed, and return the required JSON.
"""


# ── Strip fences ──────────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text[text.index("\n") + 1:] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return text.strip()


def _parse_json_response(raw: str) -> dict | None:
    """Try to extract and parse a JSON object from LLM response."""
    import json
    import re

    raw = raw.strip()

    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown fences
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding the outermost { ... }
    start = raw.find("{")
    end   = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _validate_syntax(code: str) -> tuple[bool, str]:
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, str(e)


# ── Main agent ────────────────────────────────────────────────────────────────

def connector_agent(state: dict) -> dict:
    """
    LangGraph node: for each replacement decision, analyze call sites,
    generate wrapper functions, and inject them inline into the source.
    """
    from smart_loader.core.state import AgentEvent, LoadDecision, EquivalenceGroup

    console.rule("[bold blue]🔌 Connector Agent — Inline I/O Transformer")

    decisions:    list[LoadDecision]    = state.get("decisions", [])
    groups:       list[EquivalenceGroup] = state.get("equivalence_groups", [])
    source_code:  str                   = state.get("source_code", "")
    patched_code: str                   = state.get("patched_code", source_code)
    provider:     str                   = state.get("llm_provider", "ollama")
    model:        str                   = state.get("model", "qwen3-coder-next")
    session_id:   str                   = state.get("_session_id", "unknown")
    source_file:  str                   = state.get("source_file", "")
    trace = list(state.get("agent_trace", []))
    trace.append(AgentEvent(stage="connector", event="started"))

    replacements = [d for d in decisions if d.winner != d.original]

    if not replacements:
        console.print("[green]No package replacements — no connectors needed.[/green]")
        return {
            "connectors":   {},
            "patched_code": patched_code,
            "agent_trace":  trace,
            "messages":     [AIMessage(content="Connector: no replacements.")],
        }

    llm = _get_llm(provider, model)
    connectors: dict[str, dict] = {}
    # Analyze ORIGINAL source so call sites of hashlib/json/etc. are still present.
    working_source = source_code

    for decision in replacements:
        original = decision.original
        winner   = decision.winner
        role     = decision.role

        # Get pitfalls from the equivalence group
        group = next((g for g in groups if g.role == decision.role), None)
        pitfalls_text = "\n".join(group.pitfalls) if group and group.pitfalls else decision.rationale[:300]

        console.print(
            f"\n[bold]Analyzing:[/bold] [red]{original}[/red] → [green]{winner}[/green]"
        )

        # ── Extract every call site of the original package ────────────────────
        call_sites = _extract_call_sites(working_source, original)

        if not call_sites:
            console.print(f"  [dim]No call sites found for {original} — skipping.[/dim]")
            continue

        call_sites_str = "\n".join(
            f"  Line {cs['line']}: {cs['source_line']}"
            for cs in call_sites
        )
        console.print(f"  Found [cyan]{len(call_sites)}[/cyan] call site(s):")
        for cs in call_sites:
            console.print(f"    [dim]L{cs['line']}:[/dim] {cs['source_line']}")

        # ── LLM: analyze + generate inline wrappers + rewrite source ──────────
        t0 = time.time()
        try:
            resp = llm.invoke([
                SystemMessage(content=ANALYSIS_SYSTEM),
                HumanMessage(content=ANALYSIS_HUMAN.format(
                    original=original,
                    winner=winner,
                    role=role,
                    pitfalls=pitfalls_text,
                    call_sites=call_sites_str,
                    source_code=working_source,
                )),
            ])
            latency = (time.time() - t0) * 1000

            # Token tracking
            try:
                from smart_loader.core.token_tracker import record_usage
                usage = getattr(resp, "usage_metadata", None) or {}
                record_usage(
                    session_id=session_id, agent="connector",
                    model=model, provider=provider,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    latency_ms=latency,
                )
                console.print(
                    f"  [dim]Tokens — in:{usage.get('input_tokens',0):,} "
                    f"out:{usage.get('output_tokens',0):,} "
                    f"latency:{latency:.0f}ms[/dim]"
                )
            except Exception:
                pass

            # ── Parse JSON response ────────────────────────────────────────────
            result = _parse_json_response(resp.content)

            if not result:
                console.print(f"[yellow]  ⚠ Could not parse JSON response — skipping {original}[/yellow]")
                console.print(f"  [dim]Raw response (first 300 chars): {resp.content[:300]}[/dim]")
                continue

            analysis    = result.get("analysis", [])
            wrappers    = result.get("wrappers", [])

            # Show analysis summary
            console.print(f"\n  [bold]Compatibility analysis:[/bold]")
            for item in analysis:
                icon = "[green]✓[/green]" if item.get("compatible") else "[red]✗[/red]"
                console.print(
                    f"    {icon} L{item.get('line','?')}: {item.get('call','?')[:60]}"
                    f"  — {item.get('reason','')[:80]}"
                )

            if not wrappers:
                console.print(f"  [green]✓ All {original} call sites are fully compatible — no wrappers needed[/green]")
                connectors[original] = {"functions": [], "call_rewrites": [], "compatible": True}
                continue

            # Show wrappers
            console.print(f"\n  [bold]Generated {len(wrappers)} wrapper(s):[/bold]")
            for w in wrappers:
                console.print(f"    [cyan]{w['name']}[/cyan]  replaces: {w.get('replaces','?')[:60]}")
                console.print(
                    Panel(
                        Syntax(w["code"], "python", theme="monokai", line_numbers=True),
                        title=f"[cyan]{w['name']}[/cyan]",
                        border_style="blue",
                    )
                )

            # ── Apply new source using AST inline replacement ──────────────────
            console.print(f"  [yellow]Applying AST-based inline replacement...[/yellow]")
            working_source = _inject_wrappers(
                working_source, original, winner, wrappers
            )

            connectors[original] = {
                "functions":     [w["name"] for w in wrappers],
                "call_rewrites": [{"replaces": w.get("replaces"), "with": w.get("with")} for w in wrappers],
                "compatible":    False,
            }

        except Exception as e:
            import traceback
            console.print(f"[red]  Connector failed for {original}: {e}[/red]")
            console.print(f"[dim]{traceback.format_exc()[:400]}[/dim]")

    # Apply drop-in import rewrites for fully compatible migrations.
    drop_in = {
        d.original: d.winner
        for d in replacements
        if connectors.get(d.original, {}).get("compatible")
    }
    if drop_in:
        console.print(f"\n[bold]Applying drop-in import rewrites:[/bold] {drop_in}")
        try:
            working_source = apply_import_replacements(working_source, drop_in)
        except Exception as e:
            console.print(f"[yellow]Drop-in import rewrite failed: {e}[/yellow]")

    # Re-save telemetry with the connector-patched source.
    try:
        from smart_loader.core.telemetry import save_session
        saved_id = save_session(
            source_file=source_file,
            decisions=decisions,
            agent_trace=trace,
            patched_code=working_source,
            llm_provider=provider,
            model=model,
        )
        console.print(f"\n[dim]Session saved: {saved_id}[/dim]")
    except Exception as e:
        console.print(f"[yellow]Telemetry save failed: {e}[/yellow]")

    # ── Show final patched source ──────────────────────────────────────────────
    if working_source != source_code:
        console.print(
            Panel(
                Syntax(working_source, "python", theme="monokai", line_numbers=True),
                title="[bold cyan]Patched Source (with inline adapters)[/bold cyan]",
                border_style="cyan",
            )
        )

    trace.append(AgentEvent(
        stage="connector", event="completed",
        detail={"connectors_generated": list(connectors.keys())},
    ))

    console.print(
        f"\n[bold blue]✓ Connector Agent complete — "
        f"{sum(1 for c in connectors.values() if not c.get('compatible'))} "
        f"package(s) needed adapters[/bold blue]\n"
    )

    return {
        "connectors":   connectors,
        "patched_code": working_source,
        "agent_trace":  trace,
        "messages": [AIMessage(
            content=f"Connector: {len(connectors)} package(s) analyzed, "
                    + ", ".join(
                        f"{o}({'compatible' if c.get('compatible') else str(len(c.get('functions',[]))) + ' wrappers'})"
                        for o, c in connectors.items()
                    )
        )],
    }


# ── Manual injection fallback ─────────────────────────────────────────────────

def _inject_wrappers(
    source: str,
    original: str,
    winner: str,
    wrappers: list[dict],
) -> str:
    """
    Inject wrapper function definitions directly after the last
    import statement in the source file, and use AST to find and
    replace call sites precisely.
    """
    # 1. Build a map of AST-normalized original expressions to their new replacement strings
    ast_replacements = {}
    string_replacements = {}
    for w in wrappers:
        replaces_str = w.get("replaces", "")
        with_str = w.get("with", "")
        if replaces_str and with_str:
            string_replacements[replaces_str] = with_str
            try:
                orig_ast = ast.parse(replaces_str.strip(), mode="eval").body
                ast_replacements[ast.unparse(orig_ast)] = with_str.strip()
            except Exception:
                pass

    # 2. Replace the call sites using AST offsets
    patched = source
    try:
        tree = ast.parse(patched)
        matches = []
        for node in ast.walk(tree):
            if isinstance(node, ast.expr):
                try:
                    node_str = ast.unparse(node)
                    if node_str in ast_replacements:
                        matches.append((
                            node.lineno, node.col_offset,
                            node.end_lineno, node.end_col_offset,
                            ast_replacements[node_str]
                        ))
                except Exception:
                    pass

        # Sort matches in reverse order so replacements don't shift offsets of subsequent matches
        matches.sort(key=lambda x: (x[0], x[1]), reverse=True)
        
        if matches:
            lines = patched.splitlines(keepends=True)
            for start_line, start_col, end_line, end_col, new_text in matches:
                start_idx = start_line - 1
                end_idx = end_line - 1
                if start_idx == end_idx:
                    line = lines[start_idx]
                    lines[start_idx] = line[:start_col] + new_text + line[end_col:]
                else:
                    first_line = lines[start_idx]
                    last_line = lines[end_idx]
                    new_first = first_line[:start_col] + new_text + last_line[end_col:]
                    lines[start_idx] = new_first
                    for i in range(start_idx + 1, end_idx + 1):
                        lines[i] = ""
            patched = "".join(lines)
            console.print(f"  [green]✓ AST-based replacement applied ({len(matches)} matches)[/green]")
        else:
            # Fallback to string replacement
            for rep, new_val in string_replacements.items():
                if rep in patched:
                    patched = patched.replace(rep, new_val)
                    console.print(f"  [dim]String Replaced: {rep} → {new_val}[/dim]")
    except SyntaxError:
        # Fallback to string replacement
        for rep, new_val in string_replacements.items():
            if rep in patched:
                patched = patched.replace(rep, new_val)
                console.print(f"  [dim]String Replaced: {rep} → {new_val}[/dim]")

    # 3. Inject the wrapper definitions after the last import
    try:
        tree = ast.parse(patched)
        last_import_idx = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                last_import_idx = max(last_import_idx, node.lineno)
    except SyntaxError:
        last_import_idx = 0

    header = (
        f"\n\n# {'─' * 70}\n"
        f"# AXIOM CONNECTOR: {original} → {winner}\n"
        f"# The following functions bridge API incompatibilities between\n"
        f"# {original} and {winner}. They are injected by the AXIOM\n"
        f"# connector agent and called in place of the original API.\n"
        f"# {'─' * 70}\n"
    )

    if wrappers:
        wrapper_code = "\n\n".join(w["code"] for w in wrappers)
        injection = header + wrapper_code + "\n"
        
        lines = patched.splitlines()
        # insert_at is 0-indexed, lineno is 1-indexed, so lineno is the index of the line AFTER the import
        insert_at = last_import_idx
        lines.insert(insert_at, injection)
        patched = "\n".join(lines)

    valid, err = _validate_syntax(patched)
    if not valid:
        console.print(f"  [red]Injection resulted in syntax error: {err} — reverting[/red]")
        return source

    return patched