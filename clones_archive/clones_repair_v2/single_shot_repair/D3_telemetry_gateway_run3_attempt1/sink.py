# CHOSEN-TARGET: gmqtt
"""sink -- MQTT publication of estimated telemetry records.



"""
import asyncio
import json


def _default_client(host: str = "localhost", port: int = 1883):
    from gmqtt import Client as GmqttClient  # lazy import for testability

    client = GmqttClient(client_id=None)

    async def _connect():
        await client.connect(host, port)

    _run_coro(_connect())
    return client


def _run_coro(coro):
    """Run *coro* to completion, working whether or not a loop is already running."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        # If a loop is already running we cannot use run_until_complete; schedule
        # and wait via a fresh loop in a thread-free manner is out of scope here,
        # so fall back to creating a task and running a nested loop via asyncio.run
        # is not possible. Use ensure_future + run in a new loop instead.
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()
    else:
        return loop.run_until_complete(coro)


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        topic = f"{self.topic_base}/{tag}"

        result = self.client.publish(topic, payload)

        if asyncio.iscoroutine(result):
            info = _run_coro(result)
        else:
            info = result

        return getattr(info, "rc", 0) if info is not None else 0
