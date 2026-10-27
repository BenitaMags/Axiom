"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute a CRC-32 checksum of *payload*, matching zlib.crc32's contract.

    zlib.crc32(payload) returns the standard CRC-32 (ISO-HDLC) checksum as an
    unsigned 32-bit integer (it internally masks the result so it is never
    negative, even on platforms/versions where the raw C computation could
    yield a signed value).

    fastcrc's equivalent, fastcrc.crc32.iso_hdlc(payload), implements the same
    CRC-32 algorithm/polynomial, but its return type/signedness is not
    guaranteed to match zlib's unsigned-int contract across platforms.

    This wrapper calls fastcrc.crc32.iso_hdlc(payload) and then masks the
    result with 0xFFFFFFFF to force it into the unsigned 32-bit range,
    reproducing zlib.crc32's documented return contract exactly.

    Note: unlike zlib.crc32, this wrapper does not support an incremental
    starting-value argument; fastcrc's iso_hdlc does not expose an equivalent
    parameter, so streaming/incremental CRC computation is not available here.
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
