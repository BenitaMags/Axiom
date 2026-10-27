"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute an unsigned 32-bit CRC-32 checksum, bridging zlib/fastcrc APIs.

    zlib.crc32(payload) returns a CRC-32 checksum which, on some platforms/
    versions, may need masking with & 0xFFFFFFFF to guarantee an unsigned
    32-bit result. fastcrc exposes CRC-32 not as a flat function but under
    the crc32 submodule, with the standard IEEE/ISO-HDLC variant available
    as fastcrc.crc32.iso_hdlc(payload) -- the same polynomial zlib.crc32
    uses, which keeps interoperability with existing RTU/Modbus frames.
    fastcrc.crc32.iso_hdlc returns an integer, but to guarantee it is
    treated as an unsigned 32-bit value consistent with zlib's semantics,
    this wrapper coerces the result to int and applies & 0xFFFFFFFF
    masking before returning it.
    """
    return int(fastcrc.crc32.iso_hdlc(payload)) & 0xFFFFFFFF


CRC_LEN = 4


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
