# CHOSEN-TARGET: gmqtt
"""sink -- MQTT publication of estimated telemetry records.



"""
import asyncio
import json

import gmqtt


def _default_client(host: str = "localhost", port: int = 1883):
    client = _GmqttSyncClient(host, port)
    return client


class _GmqttSyncClient:
    """Thin synchronous adapter around gmqtt's asyncio-based client.

    gmqtt is natively asynchronous, but the rest of this codebase expects a
    synchronous `.publish(topic, payload)` call (mirroring the old paho-mqtt
    usage). This adapter owns a private event loop and drives the async
    gmqtt client through it, presenting a synchronous facade.
    """

    def __init__(self, host: str = "localhost", port: int = 1883):
        self.host = host
        self.port = port
        self._loop = asyncio.new_event_loop()
        self._client = gmqtt.Client(client_id=f"sink-{id(self)}")
        self._connected = False
        self._connect()

    def _connect(self):
        async def _do_connect():
            await self._client.connect(self.host, self.port)

        self._loop.run_until_complete(_do_connect())
        self._connected = True

    def publish(self, topic: str, payload: bytes):
        self._client.publish(topic, payload)

        class _Info:
            rc = 0

        return _Info()

    def __del__(self):
        try:
            if self._connected:
                async def _do_disconnect():
                    await self._client.disconnect()

                self._loop.run_until_complete(_do_disconnect())
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
