"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute a zlib.crc32-compatible CRC-32 checksum using fastcrc.

    zlib.crc32(payload) returns a signed/unsigned-ambiguous Python int
    representing the CRC-32 (ISO-HDLC / "CRC-32") checksum of *payload*,
    which callers traditionally mask with ``& 0xFFFFFFFF`` to obtain an
    unsigned 32-bit value.

    fastcrc exposes the equivalent algorithm as ``fastcrc.crc32.iso_hdlc``,
    which takes the same single ``bytes`` argument but is a differently
    named, differently structured entry point (no top-level ``crc32``
    callable taking positional bytes the way zlib does).

    This wrapper calls ``fastcrc.crc32.iso_hdlc(payload)`` and masks the
    result with ``& 0xFFFFFFFF`` so callers get the exact same
    zlib.crc32-compatible unsigned 32-bit integer contract regardless of
    which backend computed it.
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
