"""
core/llm_json.py
──────────────────
Shared, robust JSON extraction for LLM responses.

WHY THIS EXISTS
───────────────
Reasoning models (NVIDIA Nemotron Ultra/Super, DeepSeek-R1 style models, etc.)
emit a chain-of-thought block before their actual answer. Depending on the
provider/proxy, that block shows up one of two ways:

  1. Inline in `message.content`, wrapped in <think>...</think> tags
     (this is what happens by default on NVIDIA's hosted NIM endpoint for
     nemotron-3-ultra-550b-a55b unless `chat_template_kwargs.enable_thinking`
     is explicitly disabled).
  2. Separated into a `reasoning_content` field, with the final answer
     left clean in `message.content`.

The old per-agent parsers in this codebase only ever looked at `.content`
and did naive `str.find("[")` / `str.find("{")` bracket matching. If the
model's scratch reasoning happens to *mention* JSON-like fragments — e.g.
"I should produce something like [{"role": "http"}]..." — the bracket
matcher locks onto the first bracket inside the thinking block, the slice
it grabs is not valid JSON, every parse attempt throws, and the caller
silently falls back to an empty list/dict. That is the exact mechanism
behind "all imports already optimal" when running against NVIDIA models:
the resolver's LLM call succeeds and returns real equivalence groups, but
they are being thrown away during parsing before axiom_agent ever sees them.

This module fixes that by:
  - Stripping any <think>...</think> (or <reasoning>...</reasoning>) block
    BEFORE attempting any bracket matching, regardless of provider.
  - Falling back to the *last* matching bracket pair scanned from the end
    of the string, rather than the first from the start — the final
    answer is reliably the last top-level JSON value in the response,
    even if earlier prose contains stray brackets.
  - Validating that extracted text round-trips through json.loads before
    accepting it, instead of accepting the first slice that merely looks
    bracket-shaped.
"""

from __future__ import annotations

import json
import re
from typing import Any

_THINK_BLOCK_RE = re.compile(
    r"<(think|thinking|reasoning)>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
# Some providers leave an unterminated opening tag if output was truncated
# mid-thought (e.g. max_tokens cut off before </think> was emitted).
_UNCLOSED_THINK_RE = re.compile(
    r"<(think|thinking|reasoning)>.*$",
    re.DOTALL | re.IGNORECASE,
)


def strip_reasoning(text: str) -> str:
    """Remove <think>/<reasoning> blocks (closed or truncated-open) from text."""
    if not text:
        return text
    cleaned = _THINK_BLOCK_RE.sub("", text)
    # If an opening tag survived (truncated generation), drop everything
    # from that tag onward — there is no valid trailing answer to recover.
    cleaned = _UNCLOSED_THINK_RE.sub("", cleaned)
    return cleaned.strip()


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    # Drop opening fence (with optional language tag) and closing fence.
    first_newline = text.find("\n")
    if first_newline == -1:
        return text
    body = text[first_newline + 1 :]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -3]
    return body.strip()


def _find_balanced(text: str, open_ch: str, close_ch: str) -> list[str]:
    """Return every top-level balanced substring delimited by open_ch/close_ch."""
    candidates: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == open_ch:
            if depth == 0:
                start = i
            depth += 1
        elif ch == close_ch:
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    candidates.append(text[start : i + 1])
                    start = -1
    return candidates


def coerce_llm_text(content) -> str:
    """Flatten a LangChain message ``content`` into plain text.

    Newer Anthropic models (Claude 4.5/5 era) return ``message.content`` as
    a LIST of typed content blocks (``[{"type": "text", "text": ...}, ...]``)
    whenever the response has more than one block — while short single-block
    responses, and every OpenAI-compatible provider (NVIDIA NIM etc.), return
    a plain string. Found live: every substantive resolver response under
    claude-sonnet-5 crashed with ``'list' object has no attribute 'strip'``
    while trivial 4-token responses parsed fine — the classic signature of
    the list-of-blocks shape appearing only on multi-block outputs. Text is
    concatenated from string entries and ``text``-typed dict blocks;
    thinking/tool_use blocks are skipped (they are scratch work, not the
    answer — mixing them in would resurrect the exact stray-JSON problem
    this module exists to prevent).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") in (None, "text") and isinstance(block.get("text"), str):
                    parts.append(block["text"])
        return "\n".join(parts)
    return str(content) if content is not None else ""


def extract_json(raw, expect: type = list) -> Any | None:
    """
    Extract a JSON value (list or dict, per `expect`) from a raw LLM response.

    Order of attempts:
      1. Strip <think>/<reasoning> blocks, then try a direct json.loads
         on the remaining text.
      2. Strip a single markdown code fence and retry.
      3. Scan for every top-level balanced bracket/brace span in the
         (reasoning-stripped) text and try each candidate, preferring the
         LAST one that parses successfully and matches the expected type
         — the model's final answer is reliably the last JSON value
         emitted, even when earlier prose contains stray brackets.

    Returns None if nothing parses. Callers should treat None as "could
    not parse" and apply their own fallback — do NOT conflate this with
    a legitimately empty `[]`/`{}` response from the model.
    """
    # Defensive coercion at the shared entry point: callers pass
    # ``response.content`` directly, which may be a content-block list on
    # newer Anthropic models (see coerce_llm_text). Coercing here protects
    # every parse path in the codebase at once.
    raw = coerce_llm_text(raw)
    if not raw or not raw.strip():
        return None

    open_ch, close_ch = ("[", "]") if expect is list else ("{", "}")

    cleaned = strip_reasoning(raw)
    if not cleaned:
        return None

    # Attempt 1: direct parse of the cleaned text.
    try:
        value = json.loads(cleaned)
        if isinstance(value, expect):
            return value
    except json.JSONDecodeError:
        pass

    # Attempt 2: strip a markdown fence, retry direct parse.
    fenced = _strip_code_fence(cleaned)
    if fenced != cleaned:
        try:
            value = json.loads(fenced)
            if isinstance(value, expect):
                return value
        except json.JSONDecodeError:
            pass
        cleaned = fenced

    # Attempt 3: balanced-bracket scan, prefer the LAST valid candidate.
    candidates = _find_balanced(cleaned, open_ch, close_ch)
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, expect):
            return value

    return None


def parse_llm_json(
    raw: str,
    expect: type = list,
    fallback: Any = None,
    on_failure=None,
) -> Any:
    """
    Convenience wrapper: extract_json() with a fallback value and an
    optional callback invoked with the raw text when parsing fails
    (agents use this to log a warning without duplicating the logic).
    """
    if fallback is None:
        fallback = [] if expect is list else {}

    result = extract_json(raw, expect=expect)
    if result is None:
        if on_failure:
            on_failure(raw)
        return fallback
    return result
