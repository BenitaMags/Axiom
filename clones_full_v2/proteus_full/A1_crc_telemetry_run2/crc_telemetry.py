"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute CRC-32 of *payload* using fastcrc instead of zlib.

    zlib.crc32(payload) & 0xFFFFFFFF returns the CRC-32 checksum as an
    unsigned 32-bit Python int (the mask guards against zlib's historical
    habit of returning a signed value on some platforms/Python versions).

    fastcrc.crc32.iso_hdlc(payload) computes the same ISO-HDLC CRC-32
    variant (the standard CRC-32 algorithm used by zlib), but to keep the
    return value a guaranteed-unsigned 32-bit int compatible with callers
    that pack it big-endian via struct.pack(">I", ...), we mask the result
    with 0xFFFFFFFF and coerce it to int.
    """
    return int(fastcrc.crc32.iso_hdlc(payload)) & 0xFFFFFFFF


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
