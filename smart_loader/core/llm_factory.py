"""
core/llm_factory.py
────────────────────
Single source of truth for LLM instantiation across all AXIOM agents.

Supported providers
───────────────────
  ollama        : local Ollama server (default)
  claude        : Anthropic Claude API  — env: ANTHROPIC_API_KEY
  nvidia        : NVIDIA NIM            — env: NVIDIA_API_KEY
  openai        : OpenAI API            — env: OPENAI_API_KEY
  openai-compat : any OpenAI-compatible — env: OPENAI_COMPAT_API_KEY + OPENAI_COMPAT_BASE_URL

REASONING MODELS
─────────────────
Several NVIDIA-hosted models (the Nemotron family in particular, including
nemotron-3-ultra-550b-a55b) are reasoning models: by default they emit a
<think>...</think> chain-of-thought block INSIDE the visible message content
before their actual answer. Two things follow from that:

  1. They need a much larger max_tokens budget than a normal chat model —
     a 2000-token cap can get exhausted mid-thought, before the model ever
     reaches the answer, producing truncated/unparseable output.
  2. Every agent that parses structured JSON out of `.content` MUST strip
     the <think> block first (see core/llm_json.py) rather than assuming
     `.content` is clean — this is true regardless of provider, since a
     local Ollama reasoning model would have the same issue.

We additionally pass `chat_template_kwargs={"enable_thinking": False}` for
known NVIDIA reasoning models so the NIM endpoint keeps thinking out of the
visible answer entirely when the model supports that switch. This is a
best-effort hint, not a guarantee — core/llm_json.py is the real safety net.
"""

from __future__ import annotations
import os

# ── .env support ────────────────────────────────────────────────────────────
# Load a .env file from the current working directory (and any parent dir,
# via python-dotenv's default search) BEFORE anything reads os.environ below.
# This matters because `export ANTHROPIC_API_KEY=...` typed directly into a
# terminal only lives in that one shell process — open a new terminal tab, or
# launch axiom via an IDE run button that doesn't inherit that shell's
# environment, and the key silently isn't there even though `echo $VAR` in
# the original terminal still shows it. A .env file removes that footgun:
# it's read fresh by the Python process itself, every time, regardless of
# which shell or launcher started it.
try:
    from dotenv import load_dotenv
    import pathlib as _pathlib

    def _load_env_chain():
        """Search several candidate .env locations — no find_dotenv() so it
        works on any python-dotenv version (path= arg only in 1.0+)."""
        loaded = set()
        # Walk upward from THIS FILE looking for .env (finds project root
        # even when axiom is invoked from a completely different cwd).
        here = _pathlib.Path(__file__).resolve().parent
        for parent in [here] + list(here.parents):
            candidate = parent / ".env"
            if candidate.is_file():
                load_dotenv(str(candidate), override=False)
                loaded.add(str(candidate))
                break
        # Also try cwd (covers "run from project root" without --install)
        cwd_env = _pathlib.Path.cwd() / ".env"
        if cwd_env.is_file() and str(cwd_env) not in loaded:
            load_dotenv(str(cwd_env), override=False)
            loaded.add(str(cwd_env))
        # User-level fallback: ~/.axiom.env
        user_env = _pathlib.Path.home() / ".axiom.env"
        if user_env.is_file() and str(user_env) not in loaded:
            load_dotenv(str(user_env), override=False)

    _load_env_chain()

except ImportError:
    # python-dotenv not installed — fall back to whatever the shell gave us.
    # `pip install python-dotenv` enables this; not a hard requirement.
    pass

OLLAMA = "ollama"
CLAUDE = "claude"
NVIDIA = "nvidia"
OPENAI = "openai"
COMPAT = "openai-compat"
OPENROUTER = "openrouter"

ALL_PROVIDERS = [OLLAMA, CLAUDE, NVIDIA, OPENAI, COMPAT, OPENROUTER]
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Default token budget. Reasoning models need much more headroom than this
# because chain-of-thought tokens are spent before the real answer even
# starts — see _NVIDIA_REASONING_MODEL_MARKERS below.
# 2000 was set when the only non-reasoning models in play emitted terse
# JSON. It is far too small for a modern frontier model asked to produce
# equivalence groups with reasoning and pitfalls for a file with a dozen
# imports: found live on a claude-sonnet-5 run where 5 of 12 cases logged
# EXACTLY "output: 2,000" — the cap — with the raw response cut off
# mid-token ('"reasonin' ...). The JSON was well-formed and correct; it was
# simply truncated, so extract_json could never parse it and every one of
# those cases was recorded as a resolver_miss. That is an evaluation
# artifact masquerading as a model capability result, which would have been
# a genuinely misleading number to publish. Raised to a budget that fits a
# full equivalence-group response; output tokens are only billed when
# actually generated, so this costs nothing on short replies.
DEFAULT_MAX_TOKENS = 8192
REASONING_MODEL_MAX_TOKENS = 16384

# Model name substrings that identify reasoning models needing a larger
# token budget and inline-thinking suppression. Matched case-insensitively
# against the model string (e.g. "nvidia/nemotron-3-ultra-550b-a55b").
_REASONING_MODEL_MARKERS = (
    "nemotron",
    "deepseek-r1",
    "qwq",
)


def _is_reasoning_model(model: str) -> bool:
    model_lower = model.lower()
    return any(marker in model_lower for marker in _REASONING_MODEL_MARKERS)


def _clean_key(raw: str) -> str:
    """
    Defensively strip whitespace and accidental surrounding quotes from an
    API key read out of the environment. A key copy-pasted into a .env file
    or shell profile with stray quotes (ANTHROPIC_API_KEY="sk-ant-...") or a
    trailing newline/space is a real, common source of auth failures that
    looks identical to "key not set" from the API's point of view (some
    providers return a generic 401 rather than a parse error for a
    malformed-but-present key).
    """
    if not raw:
        return raw
    cleaned = raw.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _get_llm(provider: str, model: str, max_tokens: int | None = None, temperature: float = 0.0,
             disable_thinking: bool = False):
    """
    Return a LangChain chat model for the given provider + model.
    Raises ValueError with a clear message if required env vars are missing.

    max_tokens: if not given, defaults to DEFAULT_MAX_TOKENS, except for
    known reasoning models (see _is_reasoning_model) which get
    REASONING_MODEL_MAX_TOKENS instead, since their chain-of-thought
    consumes a large share of the budget before the answer is produced.
    """
    provider = provider.lower().strip()

    if max_tokens is None:
        max_tokens = REASONING_MODEL_MAX_TOKENS if _is_reasoning_model(model) else DEFAULT_MAX_TOKENS

    if provider == OLLAMA:
        from langchain_ollama import ChatOllama
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        return ChatOllama(
            model=model, temperature=temperature,
            num_predict=max_tokens, base_url=host,
        )

    if provider == CLAUDE:
        # Re-run dotenv at call time in case the module-level load ran before
        # the .env file existed or before cwd was the project directory.
        try:
            from dotenv import load_dotenv
            import pathlib as _pathlib
            _here = _pathlib.Path(__file__).resolve().parent
            for _p in [_here] + list(_here.parents):
                _c = _p / ".env"
                if _c.is_file():
                    load_dotenv(str(_c), override=False)
                    break
            _cwd_env = _pathlib.Path.cwd() / ".env"
            if _cwd_env.is_file():
                load_dotenv(str(_cwd_env), override=False)
        except ImportError:
            pass

        api_key = _clean_key(os.environ.get("ANTHROPIC_API_KEY", ""))
        if api_key:
            # Write cleaned value back so the Anthropic SDK's lazy HTTP client
            # (initialised on first .invoke() call, not at construction time)
            # always sees a stripped, quote-free key in os.environ.
            os.environ["ANTHROPIC_API_KEY"] = api_key
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not set in this process's environment.\n"
                "This can happen even if `echo $ANTHROPIC_API_KEY` shows a key in your\n"
                "terminal — that only proves the CURRENT shell has it; a new terminal\n"
                "tab, an IDE run button, or a script launched separately may not inherit\n"
                "it. Fix options:\n"
                "  1. Put it in a .env file in your project root:\n"
                "       echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env\n"
                "     (requires `pip install python-dotenv`, auto-loaded on every run)\n"
                "  2. Add `export ANTHROPIC_API_KEY=sk-ant-...` to ~/.zshrc or ~/.bash_profile\n"
                "     (not just typed into one terminal) and open a fresh terminal.\n"
                "  3. Pass it directly: axiom run file.py --llm claude --api-key sk-ant-..."
            )
        from langchain_anthropic import ChatAnthropic
        if "claude" not in model:
            model = "claude-sonnet-4-20250514"
        
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "anthropic_api_key": api_key,  # NOTE: ChatAnthropic uses `anthropic_api_key`, NOT `api_key`
            # Bound the request at construction, exactly as the OpenRouter
            # branch does. Setting this afterwards does not work: the client is
            # built during __init__, so a later assignment updates the field
            # and leaves the live client on its default — which is how a batch
            # run sat for eight hours on one call. max_retries=1 because
            # callers do their own backoff; the default of 2 silently triples
            # every timeout.
            "timeout": float(os.environ.get("AXIOM_LLM_TIMEOUT_S", "180") or 180),
            "max_retries": 1,
        }
        # Extended thinking can consume the ENTIRE output budget before a
        # single text block is emitted. Found live during SPT generation:
        # ``stop_reason=max_tokens, content=list[thinking], out_tokens=16384``
        # — the model spent all 16k tokens reasoning and returned no answer at
        # all, which surfaced downstream as a mysterious "empty response".
        # Raising max_tokens only buys more room to think, so callers that
        # need a long, single-shot artifact (a code file, not a chat reply)
        # can turn thinking off explicitly. Applied defensively: an older
        # langchain-anthropic that does not know the parameter would raise on
        # construction, so fall back to leaving it unset.
        if disable_thinking:
            kwargs["thinking"] = {"type": "disabled"}
        # Omit temperature for models that deprecate/disallow it. The old
        # check was a single hardcoded exception ("opus-4-8"), which broke
        # the moment a new model family arrived — found live: every single
        # LLM call under claude-sonnet-5 failed with a 400
        # "`temperature` is deprecated for this model", killing all 12 cases
        # as pipeline_error before the resolver could run. The Claude 4.5+
        # and 5-era models deprecate the parameter; rather than chasing an
        # allowlist, only pass temperature for model families known to
        # accept it (3.x, and 4.0/4.1-era snapshots), and default to
        # omitting it otherwise — the API's own default is used, and a
        # missing temperature is never a 400.
        _accepts_temperature = any(
            marker in model
            for marker in ("claude-3", "claude-opus-4-0", "claude-opus-4-1",
                           "claude-sonnet-4-0", "claude-sonnet-4-2025",
                           "claude-haiku-4-0")
        )
        if _accepts_temperature:
            kwargs["temperature"] = temperature

        try:
            llm = ChatAnthropic(**kwargs)
        except TypeError:
            # Older langchain-anthropic without the `thinking` parameter.
            kwargs.pop("thinking", None)
            try:
                llm = ChatAnthropic(**kwargs)
            except TypeError:
                # ...or without `timeout`/`max_retries`. Losing the bound is
                # worse than not having it, but refusing to construct at all is
                # worse still — the caller's wall-clock guard still applies.
                kwargs.pop("timeout", None)
                kwargs.pop("max_retries", None)
                llm = ChatAnthropic(**kwargs)

        # Hard verification: confirm the key we just built the client with is
        # actually the non-empty value we intended, not silently swallowed by
        # a default_factory/env-var fallback inside langchain-anthropic. This
        # is cheap (no network call) and turns a remote 401 buried in a
        # multi-step pipeline traceback into an immediate, local, actionable
        # error at the moment the client is constructed.
        try:
            constructed_key = llm.anthropic_api_key.get_secret_value()
        except AttributeError:
            constructed_key = getattr(llm, "anthropic_api_key", None)
        if not constructed_key:
            raise ValueError(
                "ChatAnthropic was constructed but its api_key came back empty — "
                "this indicates a langchain-anthropic version mismatch rather than "
                "a missing env var (we already confirmed ANTHROPIC_API_KEY was set "
                "above). Try: pip install -U langchain-anthropic"
            )

        return llm

    if provider == NVIDIA:
        api_key = _clean_key(os.environ.get("NVIDIA_API_KEY", ""))
        if not api_key:
            raise ValueError(
                "NVIDIA_API_KEY not set.\n"
                "Get a free key at https://build.nvidia.com then:\n"
                "  export NVIDIA_API_KEY=nvapi-..."
            )
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("pip install langchain-openai")

        kwargs = {
            "model": model,
            "openai_api_key": api_key,
            "openai_api_base": NVIDIA_BASE_URL,
            "max_tokens": max_tokens,  # reasoning models get REASONING_MODEL_MAX_TOKENS, see above
            "temperature": temperature,
        }
        # Best-effort: ask NVIDIA's hosted endpoint to keep chain-of-thought
        # out of the visible answer for known reasoning models. If the
        # underlying model/profile doesn't support this flag it is simply
        # ignored by the server — core/llm_json.py still strips any <think>
        # block that leaks through regardless of whether this hint is honored.
        if _is_reasoning_model(model):
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": False},
            }
        return ChatOpenAI(**kwargs)

    if provider == OPENAI:
        api_key = _clean_key(os.environ.get("OPENAI_API_KEY", ""))
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set.")
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("pip install langchain-openai")
        return ChatOpenAI(
            model=model, openai_api_key=api_key,
            max_tokens=max_tokens, temperature=temperature,
        )

    if provider == OPENROUTER:
        # OpenRouter is OpenAI-compatible, so this could go through COMPAT with
        # the right env vars. It gets its own provider anyway because the
        # experiment records the provider in the results CSV, and a row saying
        # "openai-compat" would not say which service actually produced it —
        # the model column alone is not enough when the same model id is served
        # by several gateways at different quantisations.
        api_key = _clean_key(os.environ.get("OPENROUTER_API_KEY", ""))
        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is not set.\n"
                "Get a key at https://openrouter.ai/keys then:\n"
                "  export OPENROUTER_API_KEY=sk-or-...\n"
                "Free model ids end in ':free', e.g.\n"
                "  --model deepseek/deepseek-chat-v3.1:free\n"
                "  --model meta-llama/llama-3.3-70b-instruct:free"
            )
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("pip install langchain-openai")
        # Timeout and retry policy must be set HERE, at construction.
        # ChatOpenAI builds its underlying httpx client during __init__, so
        # assigning .request_timeout afterwards updates the pydantic field but
        # leaves the live client on its original 600s default — which is how a
        # batch run spent eight hours on one call while appearing configured.
        # max_retries=1 because this caller does its own backoff; leaving the
        # client's default of 2 silently triples every timeout.
        timeout_s = float(os.environ.get("AXIOM_LLM_TIMEOUT_S", "180") or 180)
        return ChatOpenAI(
            model=model, openai_api_key=api_key,
            openai_api_base=OPENROUTER_BASE_URL,
            max_tokens=max_tokens, temperature=temperature,
            request_timeout=timeout_s, max_retries=1,
        )

    if provider == COMPAT:
        api_key  = _clean_key(os.environ.get("OPENAI_COMPAT_API_KEY", ""))
        base_url = os.environ.get("OPENAI_COMPAT_BASE_URL", "")
        if not api_key or not base_url:
            raise ValueError(
                "Set OPENAI_COMPAT_API_KEY and OPENAI_COMPAT_BASE_URL for openai-compat provider."
            )
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("pip install langchain-openai")
        return ChatOpenAI(
            model=model, openai_api_key=api_key,
            openai_api_base=base_url,
            max_tokens=max_tokens, temperature=temperature,
        )

    raise ValueError(
        f"Unknown provider: '{provider}'. Choose from: {', '.join(ALL_PROVIDERS)}"
    )