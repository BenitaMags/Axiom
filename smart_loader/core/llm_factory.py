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
"""

from __future__ import annotations
import os

OLLAMA = "ollama"
CLAUDE = "claude"
NVIDIA = "nvidia"
OPENAI = "openai"
COMPAT = "openai-compat"

ALL_PROVIDERS = [OLLAMA, CLAUDE, NVIDIA, OPENAI, COMPAT]
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


def _get_llm(provider: str, model: str, max_tokens: int = 2000, temperature: float = 0.0):
    """
    Return a LangChain chat model for the given provider + model.
    Raises ValueError with a clear message if required env vars are missing.
    """
    provider = provider.lower().strip()

    if provider == OLLAMA:
        from langchain_ollama import ChatOllama
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        return ChatOllama(
            model=model, temperature=temperature,
            num_predict=max_tokens, base_url=host,
        )

    if provider == CLAUDE:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not set. Export it or pass --api-key."
            )
        from langchain_anthropic import ChatAnthropic
        if "claude" not in model:
            model = "claude-sonnet-4-20250514"
        
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "api_key": api_key,
        }
        # Omit temperature for models that deprecate/disallow it (like claude-opus-4-8)
        if "opus-4-8" not in model:
            kwargs["temperature"] = temperature
            
        return ChatAnthropic(**kwargs)

    if provider == NVIDIA:
        api_key = os.environ.get("NVIDIA_API_KEY", "")
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
        return ChatOpenAI(
            model=model,
            openai_api_key=api_key,
            openai_api_base=NVIDIA_BASE_URL,
            max_tokens=max_tokens,      # CRITICAL: nemotron default is very low
            temperature=temperature,
        )

    if provider == OPENAI:
        api_key = os.environ.get("OPENAI_API_KEY", "")
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

    if provider == COMPAT:
        api_key  = os.environ.get("OPENAI_COMPAT_API_KEY", "")
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