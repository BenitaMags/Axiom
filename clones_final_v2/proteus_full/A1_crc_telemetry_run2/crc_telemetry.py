"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute CRC-32 of *payload*, bridging zlib's API to fastcrc's.

    The original code used `zlib.crc32(payload) & 0xFFFFFFFF`, which returns
    a plain Python int representing the unsigned 32-bit CRC-32/ISO-HDLC
    checksum of *payload* (zlib.crc32 can yield a value that, on some
    platforms/versions, needs masking to guarantee it's treated as
    unsigned).

    fastcrc exposes the same CRC-32/ISO-HDLC variant as
    `fastcrc.crc32.iso_hdlc(payload)`, already returning an unsigned integer
    in the 32-bit range. We still mask with `& 0xFFFFFFFF` defensively so the
    return value is guaranteed to be a non-negative 32-bit unsigned integer,
    matching the original zlib-based contract exactly regardless of any
    future changes to fastcrc's return type/sign handling.
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
