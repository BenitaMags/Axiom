# CHOSEN-TARGET: gmqtt
"""sink -- MQTT publication of estimated telemetry records.



"""
import asyncio
import json


def _default_client(host: str = "localhost", port: int = 1883):
    import gmqtt  # lazy import for testability

    client = gmqtt.Client(client_id="")

    async def _connect():
        await client.connect(host, port)

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        asyncio.ensure_future(_connect())
    else:
        loop.run_until_complete(_connect())

    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
