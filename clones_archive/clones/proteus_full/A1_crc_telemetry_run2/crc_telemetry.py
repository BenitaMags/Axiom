"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute a CRC-32/ISO-HDLC checksum compatible with zlib.crc32.

    zlib.crc32(payload) returns an integer checksum using the standard
    CRC-32/ISO-HDLC polynomial; on Python 3 it is already an unsigned
    32-bit value, but historically (and for cross-version safety) callers
    mask it with ``& 0xFFFFFFFF``.

    fastcrc exposes the equivalent algorithm as
    ``fastcrc.crc32.iso_hdlc(data)``, but (a) it expects a concrete
    ``bytes`` object rather than accepting bytearray/memoryview
    transparently in all versions, and (b) its return value's
    signedness/width guarantees are not documented the same way as
    zlib's, so we cannot assume it is always an unsigned 32-bit int.

    This wrapper coerces the input to ``bytes`` if it is a
    ``bytearray`` or ``memoryview``, calls ``fastcrc.crc32.iso_hdlc``,
    and masks the result with ``& 0xFFFFFFFF`` to guarantee an unsigned
    32-bit integer is returned, matching zlib.crc32's contract exactly.
    """
    if isinstance(payload, (bytearray, memoryview)):
        payload = bytes(payload)
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
