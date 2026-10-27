# CHOSEN-TARGET: first-party
"""sink -- MQTT publication of estimated telemetry records.

This module previously depended on paho-mqtt. It now implements a minimal
first-party MQTT client sufficient to connect, and publish messages (QoS 0)
using the MQTT 3.1.1 wire protocol directly over a TCP socket, avoiding any
third-party MQTT dependency.
"""
import json
import socket
import struct


class _PublishResult:
    """Small shim mimicking the subset of paho-mqtt's MQTTMessageInfo used here."""

    def __init__(self, rc: int):
        self.rc = rc


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


class _MinimalMqttClient:
    """A minimal MQTT 3.1.1 client supporting CONNECT and PUBLISH (QoS 0).

    This is intentionally small: it implements only what `MqttSink` needs,
    replacing the external paho-mqtt dependency with first-party code.
    """

    def __init__(self, client_id: str = ""):
        self.client_id = client_id or "gateway-client"
        self._sock = None

    def connect(self, host: str = "localhost", port: int = 1883, keepalive: int = 60):
        self._sock = socket.create_connection((host, port))
        self._send_connect(keepalive)
        return 0

    def _send_connect(self, keepalive: int):
        protocol_name = b"MQTT"
        protocol_level = 4  # MQTT 3.1.1
        connect_flags = 0x02  # clean session
        client_id_bytes = self.client_id.encode("utf-8")

        variable_header = (
            struct.pack("!H", len(protocol_name)) + protocol_name +
            bytes([protocol_level]) +
            bytes([connect_flags]) +
            struct.pack("!H", keepalive)
        )
        payload = struct.pack("!H", len(client_id_bytes)) + client_id_bytes

        remaining = variable_header + payload
        packet = bytes([0x10]) + _encode_remaining_length(len(remaining)) + remaining
        self._sock.sendall(packet)

        # Read CONNACK (4 bytes: fixed header (2) + variable header (2))
        try:
            self._sock.recv(4)
        except OSError:
            pass

    def publish(self, topic: str, payload: bytes):
        topic_bytes = topic.encode("utf-8")
        if isinstance(payload, str):
            payload = payload.encode("utf-8")

        variable_header = struct.pack("!H", len(topic_bytes)) + topic_bytes
        remaining = variable_header + payload

        # QoS 0, no DUP, no RETAIN
        fixed_header_byte = 0x30
        packet = bytes([fixed_header_byte]) + _encode_remaining_length(len(remaining)) + remaining

        try:
            self._sock.sendall(packet)
            rc = 0
        except OSError:
            rc = 1
        return _PublishResult(rc)

    def disconnect(self):
        if self._sock is not None:
            try:
                self._sock.sendall(bytes([0xE0, 0x00]))
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def _default_client(host: str = "localhost", port: int = 1883):
    client = _MinimalMqttClient()
    client.connect(host, port)
    return client


class MqttSink:
    def __init__(self, topic_base: str = "plant/est", client=None):
        self.topic_base = topic_base
        self.client = client if client is not None else _default_client()

    def publish(self, tag: str, record: dict) -> int:
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        info = self.client.publish(f"{self.topic_base}/{tag}", payload)
        return getattr(info, "rc", 0)
