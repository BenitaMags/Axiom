# CHOSEN-TARGET: first-party
"""bridge -- publish sensor telemetry to an MQTT broker.



require glue -- hence Category B. The client is dependency-injected for testing.
"""
import socket
import struct

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishInfo:
    """Minimal stand-in for paho's MQTTMessageInfo."""

    def __init__(self, rc: int):
        self.rc = rc


class _MinimalMQTTClient:
    """A tiny first-party MQTT 3.1.1 client supporting CONNECT and PUBLISH.

    This implements just enough of the MQTT protocol to connect to a
    broker and publish messages, replacing the paho-mqtt dependency.
    """

    def __init__(self, client_id: str = ""):
        self.client_id = client_id
        self._sock = None

    def connect(self, host: str, port: int = 1883, keepalive: int = 60):
        sock = socket.create_connection((host, port))
        self._sock = sock
        self._send_connect(keepalive)
        self._read_connack()
        return 0

    def _send_connect(self, keepalive: int):
        proto_name = b"MQTT"
        proto_level = 4  # MQTT 3.1.1
        connect_flags = 0x02  # clean session
        client_id_bytes = self.client_id.encode("utf-8")

        variable_header = (
            self._encode_string(proto_name)
            + struct.pack("!B", proto_level)
            + struct.pack("!B", connect_flags)
            + struct.pack("!H", keepalive)
        )
        payload = self._encode_string(client_id_bytes)

        remaining = variable_header + payload
        packet = struct.pack("!B", 0x10) + self._encode_remaining_length(len(remaining)) + remaining
        self._sock.sendall(packet)

    def _read_connack(self):
        header = self._recv_exact(1)
        if not header:
            raise ConnectionError("No response from broker")
        remaining_length = self._read_remaining_length()
        body = self._recv_exact(remaining_length)
        if len(body) < 2:
            raise ConnectionError("Malformed CONNACK")
        return_code = body[1]
        if return_code != 0:
            raise ConnectionError(f"MQTT CONNACK error code {return_code}")

    def publish(self, topic: str, payload, qos: int = 0):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        elif payload is None:
            payload = b""

        topic_bytes = topic.encode("utf-8")
        variable_header = self._encode_string(topic_bytes)

        packet_id = None
        if qos > 0:
            packet_id = 1
            variable_header += struct.pack("!H", packet_id)

        remaining = variable_header + payload

        fixed_header_byte1 = 0x30 | ((qos & 0x03) << 1)
        packet = (
            struct.pack("!B", fixed_header_byte1)
            + self._encode_remaining_length(len(remaining))
            + remaining
        )

        try:
            self._sock.sendall(packet)
            if qos > 0:
                self._read_puback()
            rc = 0
        except OSError:
            rc = 1

        return _PublishInfo(rc)

    def _read_puback(self):
        header = self._recv_exact(1)
        if not header:
            return
        remaining_length = self._read_remaining_length()
        self._recv_exact(remaining_length)

    def _recv_exact(self, n: int) -> bytes:
        if n == 0:
            return b""
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def _read_remaining_length(self) -> int:
        multiplier = 1
        value = 0
        while True:
            byte = self._recv_exact(1)
            if not byte:
                return value
            byte = byte[0]
            value += (byte & 0x7F) * multiplier
            if (byte & 0x80) == 0:
                break
            multiplier *= 128
        return value

    @staticmethod
    def _encode_remaining_length(length: int) -> bytes:
        encoded = bytearray()
        while True:
            byte = length % 128
            length //= 128
            if length > 0:
                byte |= 0x80
            encoded.append(byte)
            if length <= 0:
                break
        return bytes(encoded)

    @staticmethod
    def _encode_string(data: bytes) -> bytes:
        return struct.pack("!H", len(data)) + data


def _default_client(cfg: BridgeConfig):
    client = _MinimalMQTTClient(client_id=cfg.client_id)
    client.connect(cfg.host, cfg.port)
    return client


class TelemetryBridge:
    def __init__(self, cfg: BridgeConfig, client=None):
        self.cfg = cfg
        self.client = client if client is not None else _default_client(cfg)

    def publish_reading(self, tag: str, reading: dict) -> dict:
        topic = topic_for(self.cfg.base_topic, tag)
        payload = encode(reading)
        info = self.client.publish(topic, payload, qos=self.cfg.qos)
        return {
            "topic": topic,
            "payload": payload,
            "qos": self.cfg.qos,
            "rc": getattr(info, "rc", 0),
        }

    def publish_batch(self, readings: dict) -> list:
        """Publish {tag: reading} pairs, returning the per-message results."""
        return [self.publish_reading(tag, r) for tag, r in readings.items()]
