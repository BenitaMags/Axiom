"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Drop-in replacement for zlib.crc32(payload) & 0xFFFFFFFF.

    zlib.crc32 returns a CRC-32/IEEE-802.3 checksum, historically masked with
    ``& 0xFFFFFFFF`` to guard against signed-int results on some Python 2
    builds. fastcrc does not expose a flat ``crc32`` function; instead it
    provides ``fastcrc.crc32.iso_hdlc``, which implements the same CRC-32
    algorithm (same polynomial and reflection settings as zlib.crc32, i.e.
    CRC-32/ISO-HDLC a.k.a. IEEE 802.3), so it is safe for existing RTU/Modbus
    frame compatibility. fastcrc already returns an unsigned integer, but we
    mask with ``& 0xFFFFFFFF`` here anyway to preserve the exact contract of
    the original zlib-based helper.
    """
    return fastcrc.crc32.iso_hdlc(payload) & 0xFFFFFFFF


def crc32(payload: bytes) -> int:
    """CRC-32 of *payload* as an unsigned 32-bit integer."""
    return _axiom_crc32_wrapper(payload)


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
