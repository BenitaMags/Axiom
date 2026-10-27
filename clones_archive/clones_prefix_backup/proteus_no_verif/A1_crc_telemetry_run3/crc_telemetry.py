"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _zlib_crc32_wrapper(payload: bytes) -> int:
    """Compatibility wrapper mimicking `zlib.crc32`'s return value using fastcrc.

    `zlib.crc32(data)` returns an unsigned 32-bit integer representing the
    CRC-32 (ISO-HDLC / standard CRC-32) checksum of `data`. `fastcrc`'s
    equivalent, `fastcrc.crc32.iso_hdlc(data)`, computes the same CRC-32
    variant but the two libraries are otherwise not drop-in compatible at
    the call-signature level, so this wrapper normalizes the fastcrc result
    to match zlib's contract: an unsigned 32-bit integer (masked with
    0xFFFFFFFF, matching zlib's own masking behavior for negative values on
    some platforms/versions).
    """
    return fastcrc.crc32.iso_hdlc(payload) & 0xFFFFFFFF


def crc32(payload: bytes) -> int:
    """CRC-32 of *payload* as an unsigned 32-bit integer."""
    return _zlib_crc32_wrapper(payload) & 0xFFFFFFFF


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
