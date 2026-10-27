"""sink -- MQTT publication of estimated telemetry records.

The MQTT client is one substitution target: `paho-mqtt` (synchronous, callback-based)
-> `aiomqtt` (async, context-managed) -> glue required. The client is injected.
"""
import asyncio
import json


def _aiomqtt_client_wrapper(mqtt_module, host: str, port: int):
    """Bridge for constructing a client instance.

    paho-mqtt: ``mqtt.Client()`` builds a bare client object with no network
    I/O performed yet; the host/port are supplied later to ``.connect()``.
    aiomqtt: ``aiomqtt.Client(hostname, port=...)`` is constructed with the
    connection target up front, and the actual connection is only opened by
    entering it as an async context manager (``async with client:``).
    This wrapper simply constructs the aiomqtt client bound to host/port
    (using the module handed in by the caller, since the import is kept
    local for testability), deferring the connection-opening step to
    ``_aiomqtt_connect_wrapper``, so callers can keep the paho-style
    "create, then connect" two-step flow.
    """
    return mqtt_module.Client(hostname=host, port=port)


def _aiomqtt_connect_wrapper(client):
    """Bridge for opening the network connection.

    paho-mqtt: ``client.connect(host, port)`` is a plain synchronous call
    that opens the socket and returns immediately, with the client then
    running its network loop in the background (e.g. via ``loop_start()``).
    aiomqtt: there is no synchronous connect method; the connection is
    opened by awaiting the client's ``__aenter__()`` coroutine inside an
    ``async with`` block, and closed by ``__aexit__()`` on exit of that
    block. This wrapper drives ``__aenter__()`` to completion with
    ``asyncio.run`` so it can be invoked from synchronous code exactly like
    the old ``.connect()`` call, returning the now-connected client.
    """
    asyncio.run(client.__aenter__())
    return client


def _aiomqtt_publish_wrapper(client, topic: str, payload: bytes):
    """Bridge for publishing a message and reporting a status code.

    paho-mqtt: ``client.publish(topic, payload)`` is synchronous and returns
    a ``MQTTMessageInfo`` object exposing an ``.rc`` attribute (0 on
    success, non-zero on failure) that callers inspect for the result.
    aiomqtt: ``client.publish(topic, payload)`` is a coroutine that returns
    ``None`` on success and raises an exception (e.g. ``aiomqtt.MqttError``)
    on failure -- there is no return-code object at all. This wrapper runs
    the coroutine synchronously via ``asyncio.run`` and translates the
    async success/exception outcome back into a small object with an
    ``.rc`` attribute (0 on success, 1 if an exception was raised), so
    existing callers that expect a paho-style return code keep working
    unchanged.
    """

    class _PublishResult:
        def __init__(self, rc: int):
            self.rc = rc

    try:
        asyncio.run(client.publish(topic, payload))
        return _PublishResult(0)
    except Exception:
        return _PublishResult(1)


def _default_client(host: str = "localhost", port: int = 1883):
    import paho.mqtt.client as mqtt  # lazy import for testability

    client = _aiomqtt_client_wrapper(mqtt, host, port)
    client = _aiomqtt_connect_wrapper(client)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = _aiomqtt_publish_wrapper(self.client, f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
