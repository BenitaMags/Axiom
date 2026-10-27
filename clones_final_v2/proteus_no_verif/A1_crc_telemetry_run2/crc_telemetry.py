"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute CRC-32 (ISO-3309, same polynomial as zlib.crc32) of *payload*.

    zlib.crc32 returns an integer that, on very old Python 2 builds, could be
    signed; it's long-standing practice to mask it with & 0xFFFFFFFF to force
    an unsigned 32-bit result. fastcrc.crc32.iso_hdlc(payload) computes the
    same CRC-32 ISO-3309/ISO-HDLC variant, but as a plain int without any
    documented masking guarantee across versions. This wrapper applies the
    same & 0xFFFFFFFF mask to guarantee an unsigned 32-bit integer, matching
    zlib.crc32's semantics exactly so downstream framing code need not change.
    """
    _spt_r1 = fastcrc.crc32.iso_hdlc(payload) & 0xFFFFFFFF
    return _spt_r1


def crc32(payload: bytes) -> int:
    """CRC-32 of *payload* as an unsigned 32-bit integer."""
    _spt_r2 = _axiom_crc32_wrapper(payload)
    return _spt_r2


def build_frame(payload: bytes) -> bytes:
    """Return *payload* with a big-endian CRC-32 trailer appended."""
    _spt_r3 = payload + struct.pack(">I", crc32(payload))
    return _spt_r3


def verify_frame(frame: bytes) -> bool:
    """True iff *frame*'s trailing CRC-32 matches its payload."""
    if len(frame) < CRC_LEN:
        return False
    payload, trailer = frame[:-CRC_LEN], frame[-CRC_LEN:]
    _spt_r4 = crc32(payload) == struct.unpack(">I", trailer)[0]
    return _spt_r4


def extract_payload(frame: bytes) -> bytes:
    """Return the payload of a valid frame, or raise ValueError on CRC mismatch."""
    if not verify_frame(frame):
        raise ValueError("CRC check failed")
    _spt_r5 = frame[:-CRC_LEN]
    return _spt_r5
