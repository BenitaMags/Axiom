"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc


def _fastcrc_crc32_wrapper(payload: bytes) -> int:
    """Bridge zlib.crc32's return value convention to fastcrc's API.

    zlib.crc32(payload) returns an unsigned 32-bit integer representing the
    CRC-32 checksum computed with the ISO-HDLC polynomial (the standard
    "CRC-32" variant used by zip, gzip, PNG, etc.).

    fastcrc does not expose a single crc32() entry point; instead it
    exposes named variants, e.g. fastcrc.crc32.iso_hdlc(payload), which
    computes the same ISO-HDLC CRC-32 algorithm as zlib.crc32 and returns
    an unsigned integer already in the 32-bit range.

    This wrapper calls the matching fastcrc.crc32.iso_hdlc() variant and
    masks the result with 0xFFFFFFFF to preserve exact parity with the
    original zlib-based code (guarding against any sign/width surprises).
    """
    return fastcrc.crc32.iso_hdlc(payload) & 0xFFFFFFFF

CRC_LEN = 4


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
