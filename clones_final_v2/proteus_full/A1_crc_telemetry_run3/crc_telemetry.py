"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

from fastcrc import crc32 as _fastcrc_crc32

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute a CRC-32 checksum compatible with zlib.crc32's output.

    zlib.crc32(payload) returns a signed-or-unsigned (Python normalizes to
    unsigned when masked) 32-bit integer using the standard CRC-32 algorithm
    (polynomial 0xEDB88320, reflected input/output, the same variant used by
    Ethernet/gzip/zip, commonly called "CRC-32/ISO-HDLC").

    fastcrc.crc32.iso_hdlc(payload) implements this exact same variant, but
    exposes it as a distinct named function rather than a generic crc32()
    call, and its return value must still be masked to 32 bits to guarantee
    an unsigned result matching zlib's contract.

    This wrapper calls fastcrc's iso_hdlc implementation and masks the
    result to 0xFFFFFFFF so callers can use it exactly like zlib.crc32.
    """
    return _fastcrc_crc32.iso_hdlc(payload) & 0xFFFFFFFF


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
