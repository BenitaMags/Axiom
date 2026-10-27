"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute a CRC-32 checksum compatible with zlib.crc32's output.

    zlib.crc32(payload) returns a CRC-32/ISO-HDLC checksum, and the call
    site historically masked it with ``& 0xFFFFFFFF`` to guarantee an
    unsigned 32-bit integer (zlib.crc32 can return a value that needs
    masking depending on platform/version).

    fastcrc.crc32.iso_hdlc(payload) computes the same CRC-32/ISO-HDLC
    variant (the standard CRC-32 polynomial used by zlib), so the
    algorithm matches exactly for on-wire frame compatibility. Its
    result is already an unsigned integer, but we keep the
    ``& 0xFFFFFFFF`` mask to preserve the exact guarantee the original
    code made about the return type/range.
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
