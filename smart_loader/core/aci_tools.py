"""
core/aci_tools.py
──────────────────
A small Agent-Computer-Interface (ACI), adopted from SWE-agent (Yang et al.,
NeurIPS 2024). SWE-agent's core finding was that LLM coding agents perform
much better against a constrained, purpose-built interface (numbered file
views, a scoped find/replace edit primitive, a scroll/goto window) than
against raw shell access — the interface itself is a big chunk of "agent
skill". AXIOM's agents mostly reason over in-memory source strings (an
AXIOM run never really "cd"s around a repo the way SWE-agent does), so this
module adapts the same three primitives to that setting instead of
reimplementing SWE-agent's full shell wrapper:

  1. view()          — numbered, windowed source view (SWE-agent's `open`/
                        `goto`/`scroll`), works on a string OR a file path.
  2. str_replace_edit — unique-match find/replace (mirrors the `str_replace`
                        tool Claude itself uses for editing files — same
                        contract: old_str must appear exactly once).
  3. search()         — grep-style pattern search across a string or a
                        directory (SWE-agent's `search_file`/`search_dir`).

Any AXIOM agent can import this module instead of hand-rolling AST offset
math or ad-hoc string slicing. Two agents use it directly today:

  - verifier_agent.py — uses view() to build the line-numbered source
    excerpt handed to the stage-2 LLM reviewer (so its "line 42" citations
    in remaining_incompatibilities line up with what a human sees too).
  - connector_agent.py — its string-replacement fallback path (used when
    AST-offset replacement fails on a syntactically-fuzzy `replaces` string
    from the LLM) now goes through str_replace_edit() instead of a bare
    `str.replace()`, which fails loudly with a clear error on a
    zero-match or multi-match `replaces` string rather than silently
    corrupting the source or replacing the wrong occurrence.

Other agents (axiom_agent, resolver_agent, ...) don't currently need file
edits and are left alone — this module is additive, not a forced rewrite.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from smart_loader.core.llm_timeout import invoke_with_timeout


ACI_TURN_TIMEOUT_S = 75
ACI_SESSION_TIMEOUT_S = 300


# ── view ──────────────────────────────────────────────────────────────────────

def view(
    source: str,
    *,
    is_path: bool = False,
    start: int | None = None,
    end: int | None = None,
    context: int = 4,
    highlight_line: int | None = None,
) -> str:
    """
    Return a numbered view of source code, SWE-agent `open <file> [line]`-style.

    source         : either raw source text, or a file path if is_path=True
    start, end     : 1-based inclusive line window. If omitted and
                      highlight_line is given, a `context`-line window is
                      centered on it. If both are omitted, the whole file
                      is shown (capped at 4000 lines to stay tool-friendly).
    highlight_line : marks one line with a leading `>` instead of a space,
                      the way SWE-agent flags the cursor / error line.
    """
    text = Path(source).read_text(encoding="utf-8") if is_path else source
    lines = text.splitlines()
    n = len(lines)

    if start is None and end is None and highlight_line is not None:
        start = max(1, highlight_line - context)
        end = min(n, highlight_line + context)
    if start is None:
        start = 1
    if end is None:
        end = min(n, 4000)
    start = max(1, start)
    end = min(n, end)

    width = len(str(end))
    out = []
    if start > 1:
        out.append(f"[{start - 1} lines above omitted]")
    for i in range(start, end + 1):
        marker = ">" if i == highlight_line else " "
        out.append(f"{marker}{i:>{width}}\t{lines[i - 1]}")
    if end < n:
        out.append(f"[{n - end} lines below omitted]")
    return "\n".join(out)


# ── str_replace_edit ────────────────────────────────────────────────────────────

@dataclass
class EditResult:
    ok: bool
    text: str            # unchanged input text when ok=False
    message: str


def str_replace_edit(text: str, old_str: str, new_str: str = "") -> EditResult:
    """
    Same contract as the str_replace tool used throughout this project:
    old_str must appear in `text` EXACTLY ONCE. Returns a clear, agent-
    readable error instead of a silent no-op or an ambiguous replacement
    when that's not true — this is the specific failure mode SWE-agent's
    ACI research flagged as costly: an edit tool that fails silently (or
    edits the wrong occurrence) burns an agent's retry budget on confusion
    rather than on the actual bug.
    """
    count = text.count(old_str)
    if count == 0:
        return EditResult(False, text, "No match found for old_str — it must appear verbatim in the source.")
    if count > 1:
        return EditResult(
            False, text,
            f"old_str is not unique ({count} occurrences) — narrow it with more surrounding context.",
        )
    return EditResult(True, text.replace(old_str, new_str, 1), "Replaced 1 occurrence.")


# ── search ────────────────────────────────────────────────────────────────────

@dataclass
class SearchHit:
    line: int
    text: str
    file: str | None = None


def search(source: str, pattern: str, *, is_path: bool = False, regex: bool = False) -> list[SearchHit]:
    """Grep-style search over a string (or file if is_path=True)."""
    text = Path(source).read_text(encoding="utf-8") if is_path else source
    matcher = re.compile(pattern) if regex else None
    hits: list[SearchHit] = []
    for i, line in enumerate(text.splitlines(), start=1):
        matched = matcher.search(line) if regex else (pattern in line)
        if matched:
            hits.append(SearchHit(line=i, text=line.strip(), file=source if is_path else None))
    return hits


def search_dir(directory: str, pattern: str, *, glob: str = "**/*.py", regex: bool = False) -> list[SearchHit]:
    """Grep-style search across every file matching `glob` under `directory`."""
    hits: list[SearchHit] = []
    for path in sorted(Path(directory).glob(glob)):
        if not path.is_file():
            continue
        try:
            hits.extend(search(str(path), pattern, is_path=True, regex=regex))
        except (UnicodeDecodeError, OSError):
            continue
    return hits


# ── ACISession — the stateful open/goto/scroll/edit/submit loop ─────────────────
#
# Everything above (view/str_replace_edit/search) is stateless — call it once,
# get a result. SWE-agent's actual ACI is stateful: the agent has a persistent
# cursor position and edits a live buffer over several turns, checking each
# edit's syntax before it's applied and getting to try again if it broke
# something. That statefulness is the part connector_agent's original one-shot
# "return a JSON blob with `replaces`/`with` strings, then we regex/AST-match
# them back into the source" design was missing — a single LLM call has to get
# the exact source string right on the first try with no feedback loop, which
# is exactly the "silent no-op or wrong-occurrence edit" failure mode SWE-agent's
# ACI research was a response to. ACISession + run_aci_edit_session below give
# connector_agent (see agents/connector_agent.py's `_run_aci_wrapper_injection`)
# a real multi-turn loop instead.

class ACISession:
    """
    A single in-memory file, opened for a multi-turn SWE-agent-style edit
    session: open/goto/scroll_down/scroll_up move a viewing window, edit
    replaces a line range and is REJECTED (with the parse error handed back
    to the caller, not raised) if it breaks Python syntax — mirroring
    SWE-agent's documented `edit` behavior exactly: "Python files will be
    checked for syntax errors after the edit. If an error is found, the edit
    will not be executed. Reading the error message ... is recommended."
    """

    def __init__(self, source: str, filename: str = "<source>", window: int = 100):
        self.filename = filename
        self.window = window
        self.lines: list[str] = source.splitlines()
        self.cursor = 1
        self.edit_count = 0
        self.history: list[str] = []

    @property
    def source(self) -> str:
        return "\n".join(self.lines) + ("\n" if self.lines else "")

    def _window_view(self, center: int | None = None) -> str:
        center = center if center is not None else self.cursor
        n = len(self.lines)
        start = max(1, center - self.window // 2)
        end = min(n, start + self.window - 1)
        return view(self.source, start=start, end=end, highlight_line=min(center, n) if n else None)

    def open(self, line_number: int | None = None) -> str:
        self.cursor = max(1, min(len(self.lines), line_number)) if line_number else 1
        return self._window_view()

    def goto(self, line_number: int) -> str:
        self.cursor = max(1, min(len(self.lines), line_number))
        return self._window_view()

    def scroll_down(self) -> str:
        self.cursor = min(len(self.lines), self.cursor + self.window)
        return self._window_view()

    def scroll_up(self) -> str:
        self.cursor = max(1, self.cursor - self.window)
        return self._window_view()

    def search_file(self, term: str) -> str:
        hits = search(self.source, term)
        if not hits:
            return f"No matches for {term!r} in {self.filename}."
        return "\n".join(f"L{h.line}: {h.text}" for h in hits[:30])

    def edit(self, start_line: int, end_line: int, replacement_text: str) -> str:
        import ast as _ast
        n = len(self.lines)
        if start_line < 1 or end_line < start_line or start_line > max(n, 1):
            return (
                f"ERROR: invalid line range {start_line}:{end_line} "
                f"(file currently has {n} lines) — edit NOT applied. "
                f"Use `open`/`goto` to check the current line numbers first."
            )
        end_line = min(end_line, n)
        new_lines = self.lines[: start_line - 1] + replacement_text.splitlines() + self.lines[end_line:]
        candidate = "\n".join(new_lines) + "\n"
        try:
            _ast.parse(candidate)
        except SyntaxError as e:
            return (
                f"ERROR: this edit introduces a syntax error and was NOT applied: "
                f"{e}. The file is unchanged — fix the replacement_text (check "
                f"indentation and the exact line range) and call `edit` again."
            )
        self.lines = new_lines
        self.cursor = start_line
        self.edit_count += 1
        self.history.append(f"edit {start_line}:{end_line}")
        return f"Edit applied to lines {start_line}:{end_line}.\n" + self._window_view(start_line)


def run_aci_edit_session(
    llm,
    session: ACISession,
    system_prompt: str,
    task_prompt: str,
    *,
    max_turns: int = 8,
    session_id: str = "unknown",
    agent_name: str = "connector",
    model: str = "",
    provider: str = "",
    source_file: str = "",
) -> tuple[str, list[dict], dict]:
    """
    Runs a bounded multi-turn tool-calling loop against `session`, exposing
    the SWE-agent command set (open/goto/scroll_down/scroll_up/search_file/
    edit/submit) as LangChain tools via llm.bind_tools(). Returns
    (final_source, transcript, token_totals).

    Raises whatever llm.bind_tools()/invoke() raises on the FIRST call only
    — callers should catch that to fall back to a non-tool-calling path for
    providers/models that don't support function calling well (some local
    Ollama models). Once the loop is running, individual tool-call failures
    are caught and turned into an error string handed back to the model
    (same pattern as ACISession.edit's syntax-error handling) rather than
    raised, since a model recovering from "that didn't work, try again" is
    the entire point of a multi-turn ACI over a one-shot call.
    """
    from langchain_core.tools import StructuredTool
    from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
    from pydantic import BaseModel, Field
    from typing import Optional

    class _OpenArgs(BaseModel):
        line_number: Optional[int] = Field(None, description="Line to center the view on. Omit to view from the top of the file.")

    class _GotoArgs(BaseModel):
        line_number: int = Field(..., description="Line number to jump the view to.")

    class _SearchArgs(BaseModel):
        term: str = Field(..., description="Text to search for in the currently open file.")

    class _EditArgs(BaseModel):
        start_line: int = Field(..., description="First line to replace (1-based, inclusive).")
        end_line: int = Field(..., description="Last line to replace (1-based, inclusive). Use start_line == end_line to replace a single line.")
        replacement_text: str = Field(..., description="Complete replacement text for lines start_line..end_line. Reproduce correct indentation exactly — this is a literal substitution, not a diff.")

    class _SubmitArgs(BaseModel):
        note: str = Field("", description="Optional short note summarizing what was changed.")

    submitted = {"done": False, "note": ""}

    def _submit(note: str = "") -> str:
        submitted["done"] = True
        submitted["note"] = note
        return "Submitted — session closed."

    tools = [
        StructuredTool.from_function(func=session.open, name="open", description="Open/view the file, optionally centered on a given line.", args_schema=_OpenArgs),
        StructuredTool.from_function(func=session.goto, name="goto", description="Move the view window to a specific line.", args_schema=_GotoArgs),
        StructuredTool.from_function(func=lambda: session.scroll_down(), name="scroll_down", description="Scroll the view window down."),
        StructuredTool.from_function(func=lambda: session.scroll_up(), name="scroll_up", description="Scroll the view window up."),
        StructuredTool.from_function(func=session.search_file, name="search_file", description="Search for a term in the currently open file.", args_schema=_SearchArgs),
        StructuredTool.from_function(
            func=session.edit, name="edit",
            description=(
                "Replace lines start_line..end_line (inclusive) with replacement_text. "
                "Checked for Python syntax errors before being applied — if it's "
                "rejected, the file is unchanged and you should fix and retry."
            ),
            args_schema=_EditArgs,
        ),
        StructuredTool.from_function(func=_submit, name="submit", description="Call this once, when all necessary edits are complete and correct, to end the session.", args_schema=_SubmitArgs),
    ]
    tools_by_name = {t.name: t for t in tools}
    bound_llm = llm.bind_tools(tools)

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=task_prompt)]
    transcript: list[dict] = []
    total_in = total_out = 0
    session_started = time.monotonic()

    for turn in range(max_turns):
        elapsed = time.monotonic() - session_started
        remaining = ACI_SESSION_TIMEOUT_S - elapsed
        if remaining <= 0:
            raise TimeoutError(
                f"ACI edit session exceeded its {ACI_SESSION_TIMEOUT_S}s limit"
            )
        t0 = time.time()
        resp = invoke_with_timeout(
            bound_llm,
            messages,
            timeout_s=max(1, min(ACI_TURN_TIMEOUT_S, int(remaining))),
        )
        latency_ms = (time.time() - t0) * 1000

        usage = getattr(resp, "usage_metadata", None) or {}
        total_in += usage.get("input_tokens", 0)
        total_out += usage.get("output_tokens", 0)
        try:
            from smart_loader.core.token_tracker import record_usage
            record_usage(
                session_id=session_id, agent=f"{agent_name}_aci", model=model, provider=provider,
                input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
                source_file=source_file, latency_ms=latency_ms,
            )
        except Exception:
            pass

        messages.append(resp)
        tool_calls = getattr(resp, "tool_calls", None) or []

        if not tool_calls:
            from smart_loader.core.llm_json import coerce_llm_text
            transcript.append({"turn": turn, "text": coerce_llm_text(resp.content)[:500]})
            if session.edit_count > 0:
                # Model stopped calling tools after making at least one real
                # edit — treat that as an implicit submit rather than looping
                # forever waiting for an explicit `submit` call some models
                # forget to make.
                break
            messages.append(HumanMessage(
                content="You must use the provided tools (open/goto/search_file/edit/submit) "
                        "to make changes — plain text replies aren't applied to the file. "
                        "Use `edit` to make your change, then `submit`."
            ))
            continue

        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("args", {}) or {}
            tool = tools_by_name.get(name)
            if tool is None:
                result = f"ERROR: unknown tool {name!r}"
            else:
                try:
                    result = tool.func(**args)
                except Exception as e:
                    result = f"ERROR running {name}: {e}"
            transcript.append({"turn": turn, "tool": name, "args": args, "result": str(result)[:1500]})
            messages.append(ToolMessage(content=str(result), tool_call_id=tc.get("id", name)))

        if submitted["done"]:
            break
    else:
        transcript.append({"turn": max_turns, "note": "max_turns reached — using current buffer state as-is"})

    return session.source, transcript, {"input_tokens": total_in, "output_tokens": total_out}
