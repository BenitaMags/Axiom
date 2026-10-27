# CHOSEN-TARGET: first-party
"""sink -- MQTT publication of estimated telemetry records.

The default client is a minimal first-party MQTT 3.1.1 client implemented on
top of the standard library `socket` module. It supports exactly what this
module needs: CONNECT and QOS-0 PUBLISH. This removes the dependency on the
third-party `paho-mqtt` package while remaining functionally equivalent for
this program's purposes (a fire-and-forget publish that returns an rc code,
0 meaning success).
"""
import json
import socket
import struct


class _MinimalMqttClient:
    """A tiny MQTT 3.1.1 client supporting CONNECT and QOS-0 PUBLISH.

    This is intentionally minimal: it implements just enough of the MQTT
    wire protocol to connect to a broker and publish messages, matching the
    subset of paho-mqtt's `Client` API used by `MqttSink` (`connect` and
    `publish`, where `publish` returns an object with an `.rc` attribute).
    """

    def __init__(self, client_id: str = ""):
        self._client_id = client_id
        self._sock = None

    def connect(self, host: str = "localhost", port: int = 1883, keepalive: int = 60):
        self._sock = socket.create_connection((host, port))
        self._send_connect(keepalive)
        self._recv_connack()
        return 0

    def _send_connect(self, keepalive: int):
        protocol_name = b"MQTT"
        protocol_level = 4  # MQTT 3.1.1
        connect_flags = 0x02  # clean session
        client_id = self._client_id.encode("utf-8")

        variable_header = (
            self._encode_string(protocol_name)
            + bytes([protocol_level])
            + bytes([connect_flags])
            + struct.pack("!H", keepalive)
        )
        payload = self._encode_string(client_id)
        remaining = variable_header + payload
        packet = bytes([0x10]) + self._encode_remaining_length(len(remaining)) + remaining
        self._sock.sendall(packet)

    def _recv_connack(self):
        header = self._recv_exact(1)
        if not header:
            return
        packet_type = header[0] >> 4
        remaining_length = self._decode_remaining_length()
        body = self._recv_exact(remaining_length)
        if packet_type != 2:
            raise ConnectionError("Unexpected packet type during CONNECT handshake")
        if len(body) >= 2 and body[1] != 0:
            raise ConnectionError(f"MQTT CONNACK returned error code {body[1]}")

    def publish(self, topic: str, payload: bytes, qos: int = 0):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")

        variable_header = self._encode_string(topic.encode("utf-8"))
        remaining = variable_header + payload
        fixed_header_byte = 0x30  # PUBLISH, QOS 0, no DUP, no RETAIN
        packet = bytes([fixed_header_byte]) + self._encode_remaining_length(len(remaining)) + remaining
        try:
            self._sock.sendall(packet)
            rc = 0
        except OSError:
            rc = 1
        return _PublishResult(rc)

    def disconnect(self):
        try:
            if self._sock is not None:
                self._sock.sendall(bytes([0xE0, 0x00]))
        except OSError:
            pass
        finally:
            if self._sock is not None:
                self._sock.close()
                self._sock = None

    @staticmethod
    def _encode_string(data: bytes) -> bytes:
        return struct.pack("!H", len(data)) + data

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

    def _decode_remaining_length(self) -> int:
        multiplier = 1
        value = 0
        while True:
            byte = self._recv_exact(1)[0]
            value += (byte & 0x7F) * multiplier
            if (byte & 0x80) == 0:
                break
            multiplier *= 128
        return value

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


class _PublishResult:
    def __init__(self, rc: int):
        self.rc = rc


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
