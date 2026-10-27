# CHOSEN-TARGET: first-party
"""bridge -- publish sensor telemetry to an MQTT broker.



require glue -- hence Category B. The client is dependency-injected for testing.
"""
import socket
import struct
import threading

from mqtt_config import BridgeConfig
from telemetry import encode, topic_for


class _PublishInfo:
    """Mimic paho-mqtt's MQTTMessageInfo minimally (only `.rc` is used)."""

    def __init__(self, rc: int = 0):
        self.rc = rc


class _MinimalMQTTClient:
    """A tiny first-party MQTT 3.1.1 client supporting CONNECT and PUBLISH.

    This implements just enough of the MQTT protocol to connect to a
    broker and publish messages, replacing the paho-mqtt dependency
    without changing the observable behaviour of TelemetryBridge.
    """

    CONNECT = 0x10
    CONNACK = 0x20
    PUBLISH = 0x30
    PUBACK = 0x40
    PUBREC = 0x50
    PUBREL = 0x62
    PUBCOMP = 0x70

    def __init__(self, client_id: str):
        self.client_id = client_id
        self._sock = None
        self._lock = threading.Lock()
        self._packet_id = 0

    def connect(self, host: str, port: int = 1883, keepalive: int = 60):
        sock = socket.create_connection((host, port), timeout=keepalive)
        self._sock = sock
        self._send_connect(keepalive)
        self._read_connack()
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

        payload = self._encode_string(self.client_id)

        variable_header = protocol_name + protocol_level + connect_flags + keepalive_bytes
        remaining = variable_header + payload

        packet = bytes([self.CONNECT]) + self._encode_remaining_length(len(remaining)) + remaining
        self._sock.sendall(packet)

    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Connection closed by broker")
            buf += chunk
        return buf

    def _read_remaining_length(self) -> int:
        multiplier = 1
        value = 0
        while True:
            byte = self._read_exact(1)[0]
            value += (byte & 0x7F) * multiplier
            if (byte & 0x80) == 0:
                break
            multiplier *= 128
        return value

    def _read_connack(self):
        packet_type = self._read_exact(1)[0]
        if packet_type != self.CONNACK:
            raise ConnectionError("Unexpected packet type during connect")
        remaining_length = self._read_remaining_length()
        self._read_exact(remaining_length)

    def _next_packet_id(self) -> int:
        with self._lock:
            self._packet_id = (self._packet_id % 65535) + 1
            return self._packet_id

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")

        packet_id = None
        flags = (qos & 0x03) << 1

        variable_header = self._encode_string(topic)
        if qos > 0:
            packet_id = self._next_packet_id()
            variable_header += struct.pack("!H", packet_id)

        remaining = variable_header + payload
        packet = bytes([self.PUBLISH | flags]) + self._encode_remaining_length(len(remaining)) + remaining

        try:
            self._sock.sendall(packet)
            if qos == 1:
                self._await_ack(self.PUBACK, packet_id)
            elif qos == 2:
                self._await_ack(self.PUBREC, packet_id)
                self._send_pubrel(packet_id)
                self._await_ack(self.PUBCOMP, packet_id)
            rc = 0
        except Exception:
            rc = 1

        return _PublishInfo(rc=rc)

    def _await_ack(self, expected_type: int, packet_id: int):
        packet_type = self._read_exact(1)[0]
        remaining_length = self._read_remaining_length()
        body = self._read_exact(remaining_length)
        if (packet_type & 0xF0) != expected_type:
            raise ConnectionError("Unexpected ack packet type")
        if len(body) >= 2:
            recv_id = struct.unpack("!H", body[:2])[0]
            if recv_id != packet_id:
                raise ConnectionError("Mismatched packet id in ack")

    def _send_pubrel(self, packet_id: int):
        variable_header = struct.pack("!H", packet_id)
        packet = bytes([self.PUBREL]) + self._encode_remaining_length(len(variable_header)) + variable_header
        self._sock.sendall(packet)

    def disconnect(self):
        if self._sock is not None:
            try:
                packet = bytes([0xE0, 0x00])
                self._sock.sendall(packet)
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


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
