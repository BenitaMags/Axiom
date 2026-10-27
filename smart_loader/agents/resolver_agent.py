"""
agents/resolver_agent.py  (UPDATED — token tracking + security awareness +
decoupled library discovery)
─────────────────────────
Same as before, plus:
  - Records input/output token counts to token_tracker after each LLM call
  - Surfaces security risk in the system prompt so the LLM weighs it
  - NEW: a decoupled DISCOVERY step that asks "what could this package
    become?" independent of what's already imported in the file, before the
    per-file grouping call. Without this, resolver could only ever propose
    packages it could already see imported in the SAME file — fine for
    "you're using two redundant libraries for one job," structurally unable
    to ever propose a genuinely external replacement a codebase hasn't
    adopted yet. That's a real, previously-undiagnosed scope gap: it made
    "detection rate" against PyMigBench close to 0% for any file that (like
    every PyMigBench "before" file, by construction) only imports the
    SOURCE library — the discovery step below is modeled directly on
    MigrateLib's own solution to the identical problem (Islam et al.,
    "MigrateLib: A Tool for End-to-End Python Library Migration", Listing 3:
    a standalone "identify alternative libraries for X" prompt, decoupled
    from any specific file's import list).
"""

from __future__ import annotations
import functools
import json
import time
import os
import sys
from rich.console import Console
from rich.tree import Tree
from smart_loader.core.llm_factory import _get_llm
from smart_loader.core.llm_json import parse_llm_json, coerce_llm_text
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from smart_loader.core.constraints import infer_group_flags
from smart_loader.core.state import ImportInfo, EquivalenceGroup, AgentEvent, LoaderState

console = Console()
NON_PYTHON_CANDIDATES = {"k6", "vegeta", "hey"}

# Distribution names cannot be used directly as Python import roots.  This
# small resolver-side table is only for identifying which candidate is the
# *currently imported source*; profiler metadata remains the source of truth
# for target import rewriting once a distribution is installed.
_DISTRIBUTION_IMPORT_ROOTS = {
    "kafka-python": "kafka", "locustio": "locust", "slackclient": "slack",
    "pycrypto": "Crypto", "pycryptodome": "Crypto", "pyyaml": "yaml",
    "cheetah3": "Cheetah", "pillow": "PIL", "python-dateutil": "dateutil",
    "ruamel.yaml": "ruamel", "beautifulsoup4": "bs4", "scikit-learn": "sklearn",
    "scikit-image": "skimage", "django-rest-framework": "rest_framework",
}
# Most standard-library imports are implementation details, not migration
# candidates.  These two are established application-library migrations that
# occur in PyMigBench and in real CLI modernization work.
_MIGRATABLE_STDLIB_IMPORTS = {"argparse", "optparse"}


def _is_stdlib_import(module: str) -> bool:
    """Classify standard-library roots without invoking the retired Rules Agent."""
    return module.split(".")[0].casefold() in getattr(sys, "stdlib_module_names", frozenset())


@functools.lru_cache(maxsize=1)
def _installed_import_roots() -> dict:
    """distribution name -> the import roots it actually installs.

    Read from installed package metadata rather than guessed. This is the
    authoritative answer to "what does ``paho-mqtt`` import as?" and it costs
    nothing to obtain: ``packages_distributions()`` derives ``pillow -> PIL``
    and ``pyyaml -> yaml`` correctly, which is to say it reproduces the
    hand-maintained table below without the maintenance.

    The table was twelve entries against a package index of roughly half a
    million, and every gap in it was a SILENT failure: an unbindable group was
    discarded, the connector never ran, and the program came back byte-for-byte
    identical with no error anywhere. ``paho-mqtt`` was one such gap, and it
    cost six runs across two arms.
    """
    try:
        import importlib.metadata as md
        mapping: dict = {}
        for root, dists in md.packages_distributions().items():
            for dist in dists:
                mapping.setdefault(dist.casefold(), set()).add(root)
        return mapping
    except Exception:                                     # noqa: BLE001
        # Never let a metadata problem break resolution; the heuristics below
        # still apply.
        return {}


def _candidate_matches_import(candidate: str, module: str) -> bool:
    """Return whether a distribution/candidate names the imported root."""
    root = module.split(".")[0]
    candidate_key = candidate.casefold()

    # 1. Authoritative, when the distribution is installed — which is always
    #    true for the INCUMBENT, the case this function exists to identify:
    #    the program currently runs against it.
    if root in _installed_import_roots().get(candidate_key, ()):
        return True

    # 2. The common case: the distribution name is the import name.
    normalized_candidate = candidate_key.replace("-", "_").replace(".", "_")
    normalized_root = root.casefold().replace("-", "_").replace(".", "_")
    if normalized_candidate == normalized_root:
        return True

    # 3. The hand-maintained table, now only a fallback for candidates that are
    #    not installed locally.
    return _DISTRIBUTION_IMPORT_ROOTS.get(candidate_key, "").casefold() == root.casefold()


# ── Phase 0 — decoupled library discovery ────────────────────────────────────────
# Answers "what could package X become?" using the LLM's own world knowledge,
# independent of what's currently imported in this specific file. Runs ONCE
# per unique top-level package actually imported (one combined LLM call for
# all of them, not one call each — token-cheaper and lets the model consider
# them together). Output feeds into the per-file grouping call below as
# candidate hints, not as a mandate — the per-file call still has to confirm
# a candidate is actually a fit given how the package is used in THIS file.

DISCOVERY_SYSTEM = """You are a Python package ecosystem expert. For each package
name given, identify well-known alternative PyPI packages that provide similar
functionality and could plausibly serve as a drop-in or near-drop-in replacement
in an application. Only suggest packages that are:
  - Real, published PyPI packages (use the exact PyPI distribution name)
  - Actively maintained / not abandoned
  - A genuine functional alternative — same job, not a dependency of the
    original package, not a completely different tool that happens to share
    a keyword

Respond ONLY with a JSON object mapping each input package name to a list of
2-4 alternative PyPI package names, no markdown, no preamble:
{
  "package1": ["alt1", "alt2"],
  "package2": []
}
Use an empty list if you don't know of a good alternative — do not guess or
invent package names that might not exist.
"""

DISCOVERY_HUMAN = """Find alternative libraries for each of these Python packages,
which are imported somewhere in a codebase being analyzed for optimization
opportunities:

{package_list}
"""


def _discover_alternatives(
    packages: list[str], llm, session_id: str, model: str, provider: str, source_file: str,
) -> dict[str, list[str]]:
    """Returns {package: [alternative_pypi_names]}. Never raises — a failure
    here just means no external candidates are suggested for this run;
    resolver still works exactly as it did before this feature existed."""
    if not packages:
        return {}
    try:
        t0 = time.time()
        response = llm.invoke([
            SystemMessage(content=DISCOVERY_SYSTEM),
            HumanMessage(content=DISCOVERY_HUMAN.format(package_list="\n".join(f"- {p}" for p in packages))),
        ])
        latency_ms = (time.time() - t0) * 1000
        try:
            from smart_loader.core.token_tracker import record_usage
            usage = getattr(response, "usage_metadata", None) or {}
            record_usage(
                session_id=session_id, agent="resolver_discovery", model=model, provider=provider,
                input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
                source_file=source_file, latency_ms=latency_ms,
            )
        except Exception:
            pass

        result = parse_llm_json(coerce_llm_text(response.content).strip(), expect=dict, fallback={})
        return {
            pkg: [str(alt) for alt in alts if str(alt).lower() not in NON_PYTHON_CANDIDATES][:4]
            for pkg, alts in (result or {}).items()
            if isinstance(alts, list)
        }
    except Exception as e:
        console.print(f"[yellow]⚠ Library discovery step failed ({e}) — continuing without external candidates.[/yellow]")
        return {}


SYSTEM_PROMPT = """You are an expert Python dependency analyst acting as a smart OS loader.

Your job: analyze Python imports and identify equivalence groups — clusters of packages
that serve the SAME functional purpose and where one could substitute for another.

IMPORTANT: candidates in a group do NOT have to already be imported in this file.
You will also be given a list of CANDIDATE ALTERNATIVE LIBRARIES — packages a separate
library-mapping step identified as plausible replacements for something imported here,
based on general knowledge, not on this file's specific usage. Treat these as hints to
verify, not facts: only include one in a group if, given how the source package is
ACTUALLY used in this file (see the API calls and source code below), it's genuinely a
viable substitute. Reject a suggested candidate that doesn't fit this file's real usage
rather than forcing a group just because a name was suggested.

For each group also identify:
- Known migration pitfalls (e.g. "httpx uses async by default, requests is sync")
- Which specific API methods are actually used in the code

SECURITY NOTE: Some imports may be annotated with [SECURITY:RISK CVEs:...] tags.
When a package has HIGH or CRITICAL security risk, prefer alternatives in the same
equivalence group. Always note security concerns in your reasoning.

Rules:
- Only group imports with genuine functional overlap
- Each group needs at least 2 candidates. A group can mix a package actually
  imported in this file with a candidate from the discovery step, or (rarely)
  be built entirely from discovery candidates if the file's usage pattern
  clearly supports it.
- Standard library modules should not be grouped with third-party alternatives
  UNLESS both are explicitly in the import list or one is a well-established
  drop-in replacement suggested by the discovery step.
- Be specific about roles: "async HTTP client" not just "networking"
- If a package has known CVEs and a safer equivalent exists, flag it

CRITICAL — DEPENDENCY CHAIN RULE:
  NEVER group a package with another package that it depends on, or that depends on it.
  If package A is built on top of package B (i.e. A lists B in its install_requires),
  they are NOT interchangeable alternatives — one is a layer built on the other.
  Examples of what you must NEVER group together:
    fastapi + starlette  (fastapi is built on starlette)
    flask + werkzeug     (flask is built on werkzeug)
    requests + urllib3   (requests is built on urllib3)
    boto3 + botocore     (boto3 is built on botocore)

CRITICAL — ACTIVE ROLE SEPARATION RULE:
  NEVER group two packages that are each actively serving a DIFFERENT role in the same file.
  If the code uses package A for task X AND package B for task Y, where X != Y, they
  are NOT alternatives — they are complementary. Only group them if they could
  genuinely replace each other for the SAME task.
  Example: if a file uses hashlib for cryptographic hashing AND xxhash for fast
  checksumming, those are two separate roles — do NOT group them as alternatives.
  Similarly: requests (sync HTTP) and httpx (async HTTP) used for different call sites
  in the same file are NOT alternatives if both are actively and distinctly used.

CRITICAL — INCOMPLETE MIGRATION RULE:
  Only suggest a replacement if the full migration is achievable. If replacing package A
  with package B would require rewriting class definitions, decorators, or fundamental
  architecture of the calling code (not just import lines and call sites), set
  requires_connector=true AND clearly document all the things the connector CANNOT fix.
  Do not suggest pydantic → dataclasses if the code uses pydantic BaseModel class
  inheritance and @validator decorators extensively — a connector cannot rewrite class
  inheritance.

Respond ONLY with a valid JSON array. No markdown, no preamble:
[
  {
    "role": "functional role description",
    "candidates": ["pkg1", "pkg2"],
    "used_apis": ["method1", "method2"],
    "reasoning": "why these are equivalent",
    "pitfalls": ["known migration issue 1", "known migration issue 2"],
    "security_notes": ["CVE concerns if any"],
    "crypto_required": false,
    "requires_connector": false
  }
]

If no groups exist, return: []
"""

HUMAN_TEMPLATE = """Analyze these imports and find equivalence groups.

IMPORTS (with API calls actually used in the code; [SECURITY:...] tags indicate CVE risk):
{imports_json}

CANDIDATE ALTERNATIVE LIBRARIES (from an independent library-mapping step — NOT
currently imported in this file; verify fit against actual usage below before
including any of these in a group):
{discovered_candidates}

PACKAGES DISQUALIFIED BY RULES ENGINE (not installed — exclude from groups):
{disqualified}

SECURITY SCAN SUMMARY:
{security_summary}

SOURCE CODE (first 100 lines for context):
{code_snippet}
"""

# JSON parsing now lives in core/llm_json.py — see parse_llm_json import above.
# That shared utility strips <think>/<reasoning> blocks before bracket
# matching, which the old inline version here did not do, and is the fix
# for "all imports already optimal" against reasoning models (e.g. NVIDIA
# nemotron-3-ultra) whose chain-of-thought leaked stray brackets into the
# first-bracket-to-last-bracket scan and produced an empty [] every time.

def _mandated_group(state: dict, imports) -> list:
    """A group built from an explicitly mandated substitution, or [].

    The evaluation seeds ``migration_source`` and ``migration_target`` -- the
    substitution is a REQUIREMENT, not something the resolver has to discover.
    But discovery is an LLM call at temperature 0.7, and it intermittently
    returned no group for a source module it had been handed: `json`,
    `datetime` and `smtplib` are all standard library, and the discovery prompt
    is built around finding third-party overlap. When that happened
    ``_after_resolver`` routed straight to END, so the run made ONE LLM call,
    changed nothing, and was scored a failure.

    Measured on `proteus_full`: A2 run1, C3 run1 and C3 run2 all died that way,
    while the same programs on other seeds made 6-11 calls and migrated
    successfully. Same program, same prompt, different seed, opposite outcome --
    a coin flip deciding whether the pipeline attempts the task at all.

    The mandate is already in the state, so the group is synthesised from it
    rather than asking the model again. This cannot invent work: it fires only
    when BOTH ends of the substitution were supplied by the caller, and the
    candidate it produces is the one the caller named.
    """
    source = (state.get("migration_source") or "").strip()
    target = (state.get("migration_target") or "").strip()
    if not source or not target:
        return []

    from smart_loader.core.ast_rewrite import resolve_import_root

    source_code = state.get("source_code", "") or ""
    source_import = (state.get("migration_source_import") or "").strip()
    if not source_import:
        source_import = resolve_import_root(source, source_code)

    # Only if the file really imports it. Without this check a mandate naming a
    # module the file never touches would send the rest of the pipeline looking
    # for a call site that does not exist.
    roots = {imp.module.split(".")[0].casefold() for imp in imports}
    if source_import.casefold() not in roots:
        return []

    used_apis = sorted({
        api for imp in imports
        if imp.module.split(".")[0].casefold() == source_import.casefold()
        for api in (getattr(imp, "api_calls", None) or [])
    })

    group = EquivalenceGroup(
        role=f"{source} replacement",
        candidates=[source, target],
        used_apis=used_apis,
        reasoning=(f"Substitution mandated by the caller: {source} -> {target}. "
                   f"Synthesised because discovery returned no group for this "
                   f"source, which it does intermittently for standard-library "
                   f"modules."),
        pitfalls=[],
    )
    group.__dict__["candidate_imports"] = {
        source: source_import,
        target: resolve_import_root(target, source_code),
    }
    group.__dict__["original"] = source
    group.__dict__["original_import"] = source_import
    group.__dict__["declared_source_candidate"] = source
    return [infer_group_flags(group)]


def resolver_agent(state: LoaderState) -> dict:
    console.rule("[bold yellow]🔗 Resolver Agent")

    imports:         list[ImportInfo]  = state.get("imports", [])
    security_results: dict             = state.get("security_results", {})
    source_code:     str               = state.get("source_code", "")
    llm_provider:    str               = state.get("llm_provider", "ollama")
    model:           str               = state.get("model", "qwen3-coder-next")
    session_id:      str               = state.get("_session_id", "unknown")
    migration_source: str              = state.get("migration_source", "")
    migration_source_import: str       = state.get("migration_source_import", "")
    trace = list(state.get("agent_trace", []))
    trace.append(AgentEvent(stage="resolver", event="started"))

    if not imports:
        return {
            "equivalence_groups": [],
            "agent_trace": trace,
            "messages": [AIMessage(content="Resolver: no imports, skipped.")],
        }

    imports_data = [
        {
            "module":    imp.module,
            "alias":     imp.alias,
            "names":     imp.names,
            "api_calls": imp.api_calls,
            "line":      imp.line,
        }
        for imp in imports
    ]

    code_snippet = "\n".join(source_code.splitlines()[:100])

    # Build security summary string for the prompt
    sec_lines = []
    for pkg, res in security_results.items():
        if hasattr(res, 'risk_level'):
            risk  = res.risk_level
            vulns = len(res.vulnerabilities) if hasattr(res, 'vulnerabilities') else 0
            score = getattr(res, 'overall_score', 1.0)
            sec_lines.append(f"  {pkg}: {risk} (score={score:.2f}, CVEs={vulns})")
    security_summary = "\n".join(sec_lines) or "  No security scan results available."

    llm = _get_llm(llm_provider, model)

    # ── Phase 0: decoupled library discovery ─────────────────────────────────
    # Skip discovery for ordinary stdlib modules ("what replaces os?" is not
    # a useful migration question), but retain the small explicit allowlist
    # above for real CLI-library migrations such as argparse -> click.
    top_level_packages = sorted({
        imp.module.split(".")[0] for imp in imports
        if (
            not _is_stdlib_import(imp.module)
            or imp.module.split(".")[0].casefold() in _MIGRATABLE_STDLIB_IMPORTS
        )
    })
    console.print(f"[dim]Discovering alternative libraries for: {', '.join(top_level_packages) or '(none — all stdlib)'}[/dim]")
    discovered = _discover_alternatives(top_level_packages, llm, session_id, model, llm_provider, state.get("source_file", ""))
    if discovered:
        discovered_str = "\n".join(
            f"  {pkg} -> {', '.join(alts)}" for pkg, alts in discovered.items() if alts
        ) or "  (none found)"
        console.print(f"[dim]Discovered candidates:\n{discovered_str}[/dim]")
    else:
        discovered_str = "  (discovery step found nothing, or was unavailable)"

    console.print(f"[dim]Querying {llm_provider.upper()} / {model}...[/dim]")

    t0 = time.time()
    response = llm.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=HUMAN_TEMPLATE.format(
            imports_json=json.dumps(imports_data, indent=2),
            discovered_candidates=discovered_str,
            disqualified="none — package installation is verified later in disposable environments",
            security_summary=security_summary,
            code_snippet=code_snippet,
        ))
    ])
    latency_ms = (time.time() - t0) * 1000

    # ── Token tracking ─────────────────────────────────────────────────────────
    try:
        from smart_loader.core.token_tracker import record_usage
        usage = getattr(response, "usage_metadata", None) or {}
        record_usage(
            session_id=session_id,
            agent="resolver",
            model=model,
            provider=llm_provider,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            source_file=state.get("source_file", ""),
            latency_ms=latency_ms,
        )
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        console.print(f"[dim]Tokens — input: {inp:,}  output: {out:,}  latency: {latency_ms:.0f}ms[/dim]")
    except Exception:
        pass

    raw = coerce_llm_text(response.content).strip()

    def _warn_parse_failure(raw_text: str):
        # Distinguish TRUNCATION from a genuine malformed/empty response.
        # These are completely different findings: a truncated response means
        # the model produced correct output that AXIOM's own token budget cut
        # off (an evaluation artifact, not a model failure), whereas an
        # unparseable short response is a real model result. Recording both
        # as an undifferentiated "resolver_miss" produced a materially
        # misleading number in a live run — 5 of 12 cases were truncations.
        finish = ""
        try:
            meta = getattr(response, "response_metadata", None) or {}
            finish = str(
                meta.get("stop_reason") or meta.get("finish_reason") or ""
            ).lower()
        except Exception:
            finish = ""
        truncated = finish in {"max_tokens", "length"} or (
            bool(raw_text.strip()) and finish == "" and not raw_text.rstrip().endswith(("]", "}"))
        )
        if truncated:
            console.print(
                f"[red]⚠️  LLM response was TRUNCATED by the output-token limit "
                f"(model: {model}, stop_reason: {finish or 'unterminated JSON'}). "
                f"The equivalence groups it produced could not be parsed and this "
                f"case will be recorded as a resolver miss — raise max_tokens "
                f"rather than treating this as a model capability result.[/red]\n"
                f"[dim]{raw_text[:150]!r}...[/dim]"
            )
        else:
            console.print(
                f"[yellow]⚠️  Could not extract JSON from LLM response "
                f"(model: {model}). First 150 chars:[/yellow]\n"
                f"[dim]{raw_text[:150]!r}...[/dim]"
            )

    groups_data = parse_llm_json(
        raw, expect=list, fallback=[], on_failure=_warn_parse_failure
    )

    groups: list[EquivalenceGroup] = []
    for g in groups_data:
        # A malformed element must not abort the whole run. The model
        # intermittently returns a list of STRINGS where a list of objects was
        # asked for, and `g.get(...)` then raises `AttributeError: 'str' object
        # has no attribute 'get'`, which `run_axiom_arms` records as
        # "pipeline error" and scores as a no-op with a single LLM call.
        # Measured on A1 run3: identical program, identical prompt, the other
        # two seeds migrated cleanly.
        #
        # Skipping the bad element leaves `groups` empty in the worst case, and
        # the mandate fallback below then supplies the substitution the caller
        # already specified -- so a shape surprise costs a group, not the run.
        if not isinstance(g, dict):
            console.print(f"[yellow]Discovery returned a {type(g).__name__} where an "
                          f"object was expected ({g!r:.60}); skipping it.[/yellow]")
            continue
        candidates = [c for c in g.get("candidates", [])
                      if c.lower() not in NON_PYTHON_CANDIDATES]
        if len(candidates) >= 2:
            # A resolver group is actionable only when one candidate maps to
            # an import actually present in this file.  Without this guard an
            # LLM can propose an interesting but unrelated migration and
            # AXIOM would previously treat candidates[0] as its source.
            source_index: int | None = None
            source_distribution = ""
            source_import = ""
            if migration_source:
                source_index = next(
                    (i for i, candidate in enumerate(candidates)
                     if candidate.casefold() == migration_source.casefold()),
                    None,
                )
                if source_index is None and migration_source_import:
                    source_index = next(
                        (i for i, candidate in enumerate(candidates)
                         if _candidate_matches_import(candidate, migration_source_import)),
                        None,
                    )
                source_is_imported = any(
                    _candidate_matches_import(migration_source, imp.module)
                    or (migration_source_import and imp.module.split(".")[0].casefold() == migration_source_import.casefold())
                    for imp in imports
                )
                if source_index is not None and source_is_imported:
                    source_distribution = migration_source
                    source_import = migration_source_import or candidates[source_index].replace("-", "_")
                else:
                    source_index = None
            else:
                for index, candidate in enumerate(candidates):
                    matching_import = next(
                        (imp.module.split(".")[0] for imp in imports
                         if _candidate_matches_import(candidate, imp.module)),
                        "",
                    )
                    if matching_import:
                        source_index = index
                        source_distribution = candidate
                        source_import = matching_import
                        break

            if source_index is None:
                # FAIL OPEN, not closed.
                #
                # Discarding the group here produces the worst outcome the
                # pipeline can produce: the connector never runs, the file
                # comes back byte-for-byte identical, and nothing anywhere
                # records that a migration was even attempted. Nothing is
                # migrated, so nothing is verified, so nothing is learned --
                # and to every downstream measurement it is indistinguishable
                # from the resolver deciding the incumbent was already best.
                #
                # A binding failure is evidence about OUR name mapping, not
                # about the group. The model's proposal is frequently correct
                # when this fires: for the MQTT bridge it returned gmqtt,
                # amqtt and hbmqtt -- three real MQTT clients -- and the group
                # was dropped only because ``paho-mqtt`` could not be matched
                # to the import root ``paho``.
                #
                # So: proceed on the best available guess and say plainly that
                # it is a guess. A wrong guess produces a bad edit, which the
                # verifier catches and attributes; a discarded group produces
                # silence, which nothing catches.
                fallback_import = next(
                    (imp.module.split(".")[0] for imp in imports
                     if not _is_stdlib_import(imp.module)),
                    "",
                )
                if not fallback_import:
                    console.print(
                        f"[yellow]Skipping resolver group '{g.get('role', 'unknown')}': "
                        "no candidate bound to an import, and the file imports "
                        "nothing third-party to fall back on.[/yellow]"
                    )
                    continue
                source_index = 0
                source_distribution = candidates[0]
                source_import = fallback_import
                console.print(
                    f"[yellow]Could not bind any candidate in group "
                    f"'{g.get('role', 'unknown')}' to an import; proceeding with "
                    f"{source_distribution} -> import '{source_import}' "
                    f"(UNVERIFIED mapping, downstream verification applies).[/yellow]"
                )

            source_candidate = candidates.pop(source_index)
            candidates.insert(0, source_distribution)
            group = EquivalenceGroup(
                role=g.get("role", "unknown"),
                candidates=candidates,
                used_apis=g.get("used_apis", []),
                reasoning=g.get("reasoning", ""),
                pitfalls=g.get("pitfalls", []),
            )
            # Persist the explicitly identified source instead of relying on
            # LLM candidate ordering.  The first position remains source for
            # older code paths, while ``original`` makes the invariant clear.
            candidate_imports = {
                candidate: candidate.replace("-", "_")
                for candidate in candidates
            }
            candidate_imports[source_distribution] = source_import
            group.__dict__["candidate_imports"] = candidate_imports
            group.__dict__["original"] = source_distribution
            group.__dict__["original_import"] = source_import
            group.__dict__["declared_source_candidate"] = source_candidate
            groups.append(infer_group_flags(
                group,
                llm_crypto=g.get("crypto_required"),
                llm_connector=g.get("requires_connector"),
            ))

    # Discovery produces broad leads; only candidates that the file-aware
    # equivalence analysis accepts are sent to Profiler/AXIOM.  Make that
    # narrowing visible so a user does not mistake the discovery list for a
    # list of packages that will all be benchmarked.
    accepted_candidates = {
        candidate.casefold() for group in groups for candidate in group.candidates
    }
    not_advanced = {
        package: [candidate for candidate in candidates if candidate.casefold() not in accepted_candidates]
        for package, candidates in discovered.items()
        if any(candidate.casefold() not in accepted_candidates for candidate in candidates)
    }
    if not_advanced:
        detail = "; ".join(
            f"{package}: {', '.join(candidates)}"
            for package, candidates in not_advanced.items()
        )
        console.print(
            "[dim]Discovery leads not advanced to an equivalence group after file/API fit review: "
            f"{detail}[/dim]"
        )

    if groups:
        tree = Tree("[bold yellow]Equivalence Groups Found")
        for g in groups:
            branch = tree.add(f"[cyan]{g.role}[/cyan]")
            branch.add(f"Candidates : {', '.join(g.candidates)}")
            branch.add(f"Used APIs  : {', '.join(g.used_apis) or 'none detected'}")
            if g.pitfalls:
                branch.add(f"[red]Pitfalls   : {'; '.join(g.pitfalls)}[/red]")
            flags = []
            if g.crypto_required:
                flags.append("crypto_required")
            if g.requires_connector:
                flags.append("requires_connector")
            if flags:
                branch.add(f"[yellow]Constraints: {', '.join(flags)}[/yellow]")
            branch.add(f"[dim]{g.reasoning}[/dim]")
        console.print(tree)
    else:
        console.print("[green]No overlapping dependencies — nothing to optimize.[/green]")

    if not groups:
        # Discovery found nothing. If the caller mandated a substitution, the
        # answer is already known and the run must not be thrown away -- see
        # `_mandated_group`. Three `proteus_full` rows ended here after a single
        # LLM call, having changed nothing.
        groups = _mandated_group(state, imports)
        if groups:
            console.print(
                "[yellow]Discovery returned no group; using the mandated "
                f"substitution {state.get('migration_source')} -> "
                f"{state.get('migration_target')} instead.[/yellow]")
            trace.append(AgentEvent(stage="resolver", event="mandate_fallback",
                                    detail={"source": state.get("migration_source"),
                                            "target": state.get("migration_target")}))

    console.print(f"\n[green]✓ {len(groups)} equivalence group(s) found[/green]\n")

    trace.append(AgentEvent(
        stage="resolver", event="completed",
        detail={"groups": len(groups), "roles": [g.role for g in groups]},
    ))

    return {
        "equivalence_groups": groups,
        "agent_trace":  trace,
        "messages": [AIMessage(
            content=f"Resolver: {len(groups)} group(s): "
                    + ", ".join(f"'{g.role}'" for g in groups)
        )],
    }
