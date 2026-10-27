"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute a CRC-32 checksum compatible with zlib.crc32's return value.

    zlib.crc32(payload) returns a CRC-32 checksum as an integer, which callers
    typically mask with `& 0xFFFFFFFF` to guarantee an unsigned 32-bit result
    (needed on some platforms/Python versions where zlib.crc32 could yield a
    signed value). fastcrc's crc32.iso_hdlc(payload) implements the same
    polynomial/algorithm as zlib.crc32 (the standard CRC-32 "ISO-HDLC"
    variant), but to keep behavior identical to the original
    `zlib.crc32(payload) & 0xFFFFFFFF` call, we apply the same masking to the
    fastcrc result here before returning it.
    """
    return fastcrc.crc32.iso_hdlc(payload) & 0xFFFFFFFF


CRC_LEN = 4


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
