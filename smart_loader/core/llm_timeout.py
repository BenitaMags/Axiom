"""Bound synchronous LLM calls used by agent loops.

Provider clients occasionally leave an HTTP/tool-calling request open without
returning an error.  A turn limit alone does not protect an agent in that
case, because it cannot reach its next turn.  On POSIX CLI runs, SIGALRM lets
us interrupt the blocked main-thread call and return control to the pipeline.
"""

from __future__ import annotations

import signal
import threading
from typing import Any


class LLMInvocationTimeout(TimeoutError):
    """Raised when an LLM provider does not answer within the allowed time."""


def invoke_with_timeout(llm: Any, messages: list[Any], *, timeout_s: int = 75) -> Any:
    """Invoke an LLM with a hard timeout when signals are available.

    Agent calls are made on the CLI's main thread, where SIGALRM is supported
    on macOS/Linux.  Other environments retain the provider's normal timeout
    behaviour rather than failing due to unsupported signal operations.
    """
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")

    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
        return llm.invoke(messages)

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def _raise_timeout(_signum: int, _frame: object) -> None:
        raise LLMInvocationTimeout(f"LLM request exceeded {timeout_s}s")

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_s)
    try:
        return llm.invoke(messages)
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)
