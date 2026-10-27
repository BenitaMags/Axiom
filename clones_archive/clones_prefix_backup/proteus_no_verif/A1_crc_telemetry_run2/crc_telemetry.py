"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _fastcrc_crc32_wrapper(payload: bytes) -> int:
    """zlib-compatible wrapper around fastcrc's CRC-32 implementation.

    `zlib.crc32(data)` returns an integer CRC-32 checksum (which, on
    Python 3, can be treated as unsigned via `& 0xFFFFFFFF`). `fastcrc`
    exposes the equivalent algorithm as `fastcrc.crc32.iso_hdlc(data)`,
    which is the standard CRC-32 variant used by zlib. This wrapper calls
    `fastcrc.crc32.iso_hdlc` and masks the result to an unsigned 32-bit
    integer so callers written against the zlib-style signature/return
    value continue to work unchanged.
    """
    return fastcrc.crc32.iso_hdlc(payload) & 0xFFFFFFFF


def crc32(payload: bytes) -> int:
    """CRC-32 of *payload* as an unsigned 32-bit integer."""
    return _fastcrc_crc32_wrapper(payload)


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
