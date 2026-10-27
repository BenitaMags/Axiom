"""Helper glue for bridging amqtt's async client onto a synchronous API.

Kept as a separate module so the persistent-event-loop plumbing used by
`sink._AxiomMqttClientFacade` can be reused/tested independently of the
sink module itself.
"""
import asyncio
import threading


class _ThreadedLoopRunner:
    """Owns a background event loop and runs coroutines on it synchronously.

    Submitting a coroutine via `__call__` blocks the calling thread until
    the coroutine completes on the background loop, re-raising any
    exception it raised -- mirroring `run_coroutine_threadsafe(...).result()`
    semantics used previously inline.
    """

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def __call__(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def stop(self):
        self._loop.call_soon_threadsafe(self._loop.stop)


def build_amqtt_backed_client(host: str = "localhost", port: int = 1883):
    """Start a background loop, connect amqtt's MQTTClient, and return both.

    Returns a `(runner, client)` pair where `runner(coro)` executes `coro`
    on the persistent background loop and blocks for its result, and
    `client` is the connected `amqtt.client.MQTTClient` instance.
    """
    from amqtt.client import MQTTClient  # lazy import for testability

    runner = _ThreadedLoopRunner()
    client = MQTTClient()
    runner(client.connect(f"mqtt://{host}:{port}"))
    return runner, client
