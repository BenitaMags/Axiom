# CHOSEN-TARGET: gmqtt
"""sink -- MQTT publication of estimated telemetry records.



"""
import asyncio
import json


def _default_client(host: str = "localhost", port: int = 1883):
    import gmqtt  # lazy import for testability

    client = gmqtt.Client(client_id=None)

    async def _connect():
        await client.connect(host, port)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_connect())
    finally:
        asyncio.set_event_loop(loop)

    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
