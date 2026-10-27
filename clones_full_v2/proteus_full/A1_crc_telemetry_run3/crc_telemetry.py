"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute a zlib-compatible CRC-32 of *payload* using fastcrc.

    zlib.crc32(payload) returns the CRC-32 checksum of *payload* as a
    Python int, implicitly unsigned in range [0, 2**32).  fastcrc has no
    single `crc32` callable with the same signature; instead it exposes
    per-variant functions, and the variant that is bit-for-bit compatible
    with zlib's CRC-32 (and the classic CRC-32 used by Ethernet/gzip/PNG)
    is `fastcrc.crc32.iso_hdlc`. That function returns a plain int as
    well, but to preserve zlib.crc32's exact contract (an unsigned
    32-bit integer) we mask the result with 0xFFFFFFFF here, just as the
    original call site did.
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
