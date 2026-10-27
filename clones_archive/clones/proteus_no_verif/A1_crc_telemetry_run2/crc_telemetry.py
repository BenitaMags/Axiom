"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute a zlib.crc32-compatible checksum using fastcrc.

    zlib.crc32(payload) returns a Python int representing the unsigned
    32-bit CRC-32/ISO-HDLC checksum of *payload*. fastcrc exposes the same
    algorithm as fastcrc.crc32.iso_hdlc(payload), which also returns an
    int, but its sign/width guarantees are not documented as strictly as
    zlib's, and some fastcrc variants may return values outside the
    unsigned 32-bit range or bytes-like objects. To keep this a drop-in
    replacement for zlib.crc32, we call the fastcrc ISO-HDLC implementation
    (the same polynomial/reflection settings zlib uses) and mask the
    result with & 0xFFFFFFFF to guarantee an unsigned 32-bit int output,
    matching the exact contract callers already rely on.
    """

    return fastcrc.crc32.iso_hdlc(payload) & 0xFFFFFFFF


def crc32(payload: bytes) -> int:
    """CRC-32 of *payload* as an unsigned 32-bit integer."""
    return _axiom_crc32_wrapper(payload) & 0xFFFFFFFF


def build_frame(payload: bytes) -> bytes:
    """Return *payload* with a big-endian CRC-32 trailer appended."""
    return payload + struct.pack(">I", crc32(payload))


def verify_frame(frame: bytes) -> bool:
    """True iff *frame*'s trailing CRC-32 matches its payload."""
    if len(frame) < CRC_LEN:
        return False
    payload, trailer = frame[:-CRC_LEN], frame[-CRC_LEN:]
    return crc32(payload) == struct.unpack(">I", trailer)[0]


def extract_payload(frame: bytes) -> bytes:
    """Return the payload of a valid frame, or raise ValueError on CRC mismatch."""
    if not verify_frame(frame):
        raise ValueError("CRC check failed")
    return frame[:-CRC_LEN]
