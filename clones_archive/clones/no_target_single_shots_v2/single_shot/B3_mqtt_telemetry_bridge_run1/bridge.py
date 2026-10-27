# CHOSEN-TARGET: first-party
"""bridge -- publish sensor telemetry to an MQTT broker.



require glue -- hence Category B. The client is dependency-injected for testing.
"""
import socket
import struct
from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishInfo:
    """Mimics the small subset of paho-mqtt's MQTTMessageInfo used here."""

    def __init__(self, rc: int = 0):
        self.rc = rc


class _MinimalMqttClient:
    """A tiny, dependency-free MQTT 3.1.1 client supporting CONNECT/PUBLISH.

    This implements just enough of the MQTT wire protocol to connect to a
    broker and publish messages, replacing the need for paho-mqtt.
    """

    def __init__(self, client_id: str):
        self.client_id = client_id
        self._sock = None

    def connect(self, host: str, port: int = 1883, keepalive: int = 60):
        self._sock = socket.create_connection((host, port), timeout=10)
        self._send_connect(keepalive)
        return 0

    def _encode_remaining_length(self, length: int) -> bytes:
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

    def _encode_string(self, s: str) -> bytes:
        data = s.encode("utf-8")
        return struct.pack("!H", len(data)) + data

    def _send_connect(self, keepalive: int):
        protocol_name = self._encode_string("MQTT")
        protocol_level = bytes([4])  # MQTT 3.1.1
        connect_flags = bytes([0x02])  # clean session
        keepalive_bytes = struct.pack("!H", keepalive)
        client_id_bytes = self._encode_string(self.client_id)

        variable_header = protocol_name + protocol_level + connect_flags + keepalive_bytes
        payload = client_id_bytes

        remaining = variable_header + payload
        packet = bytes([0x10]) + self._encode_remaining_length(len(remaining)) + remaining

        self._sock.sendall(packet)

        # Read CONNACK (4 bytes: fixed header (2) + variable header (2))
        try:
            self._sock.settimeout(10)
            response = self._recv_exact(4)
            # response[0] == 0x20 for CONNACK; response[3] is return code
            if len(response) < 4:
                raise ConnectionError("Incomplete CONNACK received")
        except socket.timeout:
            raise ConnectionError("Timed out waiting for CONNACK")

    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        elif payload is None:
            payload = b""

        topic_bytes = self._encode_string(topic)

        variable_header = topic_bytes
        packet_id = None
        if qos > 0:
            packet_id = 1
            variable_header += struct.pack("!H", packet_id)

        remaining = variable_header + payload

        fixed_header_byte = 0x30 | ((qos & 0x03) << 1)
        packet = bytes([fixed_header_byte]) + self._encode_remaining_length(len(remaining)) + remaining

        try:
            self._sock.sendall(packet)
            rc = 0
        except OSError:
            rc = 1

        return _PublishInfo(rc=rc)

    def disconnect(self):
        if self._sock is not None:
            try:
                packet = bytes([0xE0, 0x00])
                self._sock.sendall(packet)
            except OSError:
                pass
            finally:
                self._sock.close()
                self._sock = None


def _default_client(cfg: BridgeConfig):
    client = _MinimalMqttClient(client_id=cfg.client_id)
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
