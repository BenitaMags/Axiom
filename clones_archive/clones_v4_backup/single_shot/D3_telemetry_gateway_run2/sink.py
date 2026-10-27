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
        topic = f"{self.topic_base}/{tag}"

        result = self.client.publish(topic, payload)

        if asyncio.iscoroutine(result):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(result)
                    return 0
                else:
                    result = loop.run_until_complete(result)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(result)
                finally:
                    asyncio.set_event_loop(loop)

        return getattr(result, "rc", 0) if result is not None else 0
