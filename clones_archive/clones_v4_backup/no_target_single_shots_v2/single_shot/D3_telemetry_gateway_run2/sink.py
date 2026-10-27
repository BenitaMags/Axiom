# CHOSEN-TARGET: first-party
"""sink -- MQTT publication of estimated telemetry records.

This module previously depended on `paho-mqtt`. It now ships a minimal,
first-party MQTT 3.1.1 client sufficient to CONNECT and PUBLISH (QoS 0)
messages to a broker, so no external MQTT dependency is required.

The public API (`MqttSink`) is unchanged: it accepts an optional `client`
object exposing a `publish(topic, payload)` method returning an object with
an `.rc` attribute (or any object; `getattr(info, "rc", 0)` is used), and if
no client is given it builds a default one connected to localhost:1883.
"""
import json
import socket
import struct


class _MqttPublishResult:
    """Mimics the small subset of paho-mqtt's MQTTMessageInfo used here."""

    def __init__(self, rc: int):
        self.rc = rc


class _MinimalMqttClient:
    """A tiny first-party MQTT 3.1.1 client supporting CONNECT and PUBLISH.

    This intentionally implements only what `MqttSink` needs: connecting to
    a broker and publishing QoS 0 messages. It is not a general-purpose MQTT
    client.
    """

    def __init__(self):
        self._sock = None

    def connect(self, host: str = "localhost", port: int = 1883, timeout: float = 5.0):
        sock = socket.create_connection((host, port), timeout=timeout)
        self._sock = sock
        self._send_connect()
        return 0

    def _send_connect(self):
        client_id = "estimator-sink"
        protocol_name = b"MQTT"
        protocol_level = 4  # MQTT 3.1.1
        connect_flags = 0x02  # clean session
        keep_alive = 60

        variable_header = (
            struct.pack("!H", len(protocol_name)) + protocol_name +
            struct.pack("!B", protocol_level) +
            struct.pack("!B", connect_flags) +
            struct.pack("!H", keep_alive)
        )
        client_id_bytes = client_id.encode("utf-8")
        payload = struct.pack("!H", len(client_id_bytes)) + client_id_bytes

        remaining = variable_header + payload
        packet = bytes([0x10]) + self._encode_remaining_length(len(remaining)) + remaining
        self._sock.sendall(packet)

        # Read CONNACK (4 bytes: fixed header type/flags, remaining length,
        # session present, return code). We don't strictly validate it to
        # keep this minimal, but we drain the socket to avoid buffering
        # issues on some brokers.
        try:
            self._sock.settimeout(5.0)
            self._sock.recv(4)
        except Exception:
            pass

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

    def publish(self, topic: str, payload: bytes):
        if self._sock is None:
            return _MqttPublishResult(rc=1)

        try:
            topic_bytes = topic.encode("utf-8")
            variable_header = struct.pack("!H", len(topic_bytes)) + topic_bytes
            body = variable_header + payload

            fixed_header_byte1 = 0x30  # PUBLISH, QoS 0, no DUP, no RETAIN
            packet = (
                bytes([fixed_header_byte1]) +
                self._encode_remaining_length(len(body)) +
                body
            )
            self._sock.sendall(packet)
            return _MqttPublishResult(rc=0)
        except Exception:
            return _MqttPublishResult(rc=1)


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
