# CHOSEN-TARGET: gmqtt
#
# gmqtt is an asyncio-native MQTT client with no synchronous API and no
# per-call publish return code (unlike paho-mqtt's `publish()`, which returns
# a MQTTMessageInfo with an `.rc` attribute). This module adapts gmqtt's
# async, callback-based connection lifecycle to the synchronous `.publish()`
# call that `MqttSink` (and the rest of this program) expects, running the
# necessary coroutines to completion on a dedicated event loop.
import json
import uuid
import asyncio


def _get_or_create_loop():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("event loop is closed")
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _default_client(host: str = "localhost", port: int = 1883):
    """Lazily build a connected gmqtt client wrapped in a sync-friendly adapter."""
    from gmqtt import Client as GmqttClient  # lazy import for testability

    loop = _get_or_create_loop()

    # gmqtt requires a concrete client_id string (it is encoded into the
    # CONNECT packet), unlike paho-mqtt which will generate one for you when
    # an empty string/None is supplied.
    client_id = uuid.uuid4().hex
    raw_client = GmqttClient(client_id)

    loop.run_until_complete(raw_client.connect(host, port))
    return _GmqttSyncAdapter(raw_client, loop)


class _GmqttSyncAdapter:
    """Adapts gmqtt's connection to a simple synchronous `.publish()` call.

    gmqtt's `Client.publish()` is itself a plain (non-async) method that
    enqueues the PUBLISH packet on the underlying transport -- it is not a
    coroutine and returns nothing useful. gmqtt has no notion of a per-call
    return code (unlike paho), so this adapter always reports success (0)
    once the publish has been handed to the client, matching the previous
    best-effort semantics used by MqttSink.
    """

    def __init__(self, raw_client, loop):
        self._raw = raw_client
        self._loop = loop

    def publish(self, topic: str, payload: bytes):
        result = self._raw.publish(topic, payload)
        if asyncio.iscoroutine(result):
            self._loop.run_until_complete(result)
        return _PublishResult(rc=0)


class _PublishResult:
    def __init__(self, rc: int = 0):
        self.rc = rc


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
