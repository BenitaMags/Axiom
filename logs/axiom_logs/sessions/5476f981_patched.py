"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json
import threading

import aiomqtt


def _aiomqtt_client_factory_wrapper(host: str = "localhost", port: int = 1883):
    """Bridge aiomqtt's async client into a synchronous, persistently-connected facade.

    paho-mqtt's `mqtt.Client()` + `.connect(host, port)` hands back a client
    object immediately usable for synchronous `.publish()` calls; connection
    upkeep happens on an internal network thread the library manages for you.

    aiomqtt's `Client` is an async context manager only: you must
    `async with Client(host, port) as client` to connect, and
    `await client.publish(topic, payload)` to publish -- there is no
    synchronous entry point at all.

    This wrapper starts one persistent background event loop in a daemon
    thread, enters the aiomqtt client's `async with` context on that loop, and
    returns a small synchronous facade exposing `.publish(topic, payload)` and
    `.close()`. Every call is round-tripped onto the same loop via
    `asyncio.run_coroutine_threadsafe(...).result()` rather than
    `asyncio.run(...)`, so:
      - the connection is opened once and stays valid for the sink's whole
        lifetime (a fresh `asyncio.run` per call would tear down the loop the
        connection is bound to, breaking the next call);
      - any exception raised inside the coroutine (bad topic, dropped
        connection, etc.) is re-raised in the calling thread by `.result()`,
        so failures propagate instead of being silently absorbed.
    """
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    client = aiomqtt.Client(hostname=host, port=port)

    def _run(coro):
        return asyncio.run_coroutine_threadsafe(coro, loop).result()

    _run(client.__aenter__())

    class _SyncMqttFacade:
        def publish(self, topic: str, payload: bytes) -> int:
            """Publish `payload` to `topic`, returning a paho-style rc.

            aiomqtt's `publish()` is a coroutine returning `None` on success
            (at the default qos=0) and raising on failure -- there is no
            MQTTMessageInfo/.rc to read. paho-mqtt's rc==0 meant "handed off
            to the network stack successfully" (not "acknowledged by the
            broker" at qos 0), so returning 0 once the await completes without
            raising is an honest match for that same meaning. Failures are
            not caught here: they raise straight through to the caller, exactly
            as paho-mqtt would raise (e.g. ValueError for a bad topic).
            """
            _run(client.publish(topic, payload))
            return 0

        def close(self):
            _run(client.__aexit__(None, None, None))
            loop.call_soon_threadsafe(loop.stop)

    return _SyncMqttFacade()


def _default_client(host: str = "localhost", port: int = 1883):
    return _aiomqtt_client_factory_wrapper(host, port)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        return self.client.publish(f"{self.topic_base}/{tag}", payload)
