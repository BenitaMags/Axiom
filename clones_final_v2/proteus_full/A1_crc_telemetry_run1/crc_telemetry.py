"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Wrapper bridging zlib.crc32 to fastcrc's API.

    zlib.crc32(payload) returns a CRC-32 checksum as an integer, and callers
    typically mask it with & 0xFFFFFFFF to guarantee an unsigned 32-bit
    result (needed because on some platforms/versions zlib.crc32 could
    return a signed value).

    fastcrc does not expose a `crc32` function directly compatible with
    zlib's; instead it exposes `fastcrc.crc32.iso_hdlc`, which implements
    the same CRC-32 variant (polynomial, init, reflection, xorout) as
    zlib.crc32. This wrapper calls that function and masks the result to
    an unsigned 32-bit integer, preserving zlib.crc32's exact return
    semantics for downstream code.
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
