"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute CRC-32 of *payload* using fastcrc, matching zlib.crc32's contract.

    zlib.crc32(payload) returns an integer whose sign/width can vary subtly
    across Python versions/platforms, so callers traditionally mask the
    result with `& 0xFFFFFFFF` to guarantee an unsigned 32-bit value.

    fastcrc exposes CRC-32 variants as explicit named functions rather than
    a single generic call; `fastcrc.crc32.iso_hdlc` is the ISO-HDLC/IEEE
    802.3 variant, which is the exact algorithm zlib.crc32 implements (as
    opposed to CRC-32C or other variants fastcrc also provides). It already
    returns an unsigned integer, but we keep the `& 0xFFFFFFFF` mask applied
    here anyway for defensive parity with zlib's unsigned-int contract.
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
