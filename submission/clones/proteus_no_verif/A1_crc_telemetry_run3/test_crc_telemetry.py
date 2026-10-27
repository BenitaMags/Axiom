"""Tests for crc_telemetry (dual-mode: pytest or `python3 test_crc_telemetry.py`)."""
import struct

from crc_telemetry import build_frame, crc32, extract_payload, verify_frame


def test_known_crc_value():
    # Reference CRC-32 of b"123456789" is 0xCBF43926.
    assert crc32(b"123456789") == 0xCBF43926


def test_roundtrip():
    payload = b"\x01\x03\x02\x00\x64"  # a small Modbus-like reply
    frame = build_frame(payload)
    assert verify_frame(frame)
    assert extract_payload(frame) == payload


def test_detects_corruption():
    frame = bytearray(build_frame(b"tag=PT101;val=42"))
    frame[3] ^= 0xFF  # flip a payload bit
    assert not verify_frame(bytes(frame))


def test_short_frame_is_invalid():
    assert not verify_frame(b"\x00")


def test_trailer_is_big_endian():
    payload = b"abc"
    frame = build_frame(payload)
    assert struct.unpack(">I", frame[-4:])[0] == crc32(payload)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
