"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute a CRC-32 checksum compatible with zlib.crc32.

    zlib.crc32(payload) returns a checksum using the CRC-32/ISO-HDLC
    polynomial/init/xorout, masked with & 0xFFFFFFFF to guarantee an
    unsigned 32-bit integer (zlib.crc32 can return a signed value on
    some platforms/Python versions).

    fastcrc does not expose a flat crc32() function; instead it provides
    fastcrc.crc32.iso_hdlc(payload), which implements the same
    CRC-32/ISO-HDLC variant (matching polynomial/init/xorout), so it is
    wire-compatible with frames produced by the old zlib-based code.

    This wrapper calls fastcrc.crc32.iso_hdlc and re-applies the
    & 0xFFFFFFFF mask to preserve the original crc32() function's
    contract of always returning an unsigned 32-bit int.
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
