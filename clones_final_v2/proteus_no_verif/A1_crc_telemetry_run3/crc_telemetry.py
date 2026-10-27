"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes, value: int = None) -> int:
    """Compute a CRC-32 checksum compatible with zlib.crc32(payload).

    zlib.crc32(payload) returns a CRC-32/ISO-HDLC checksum as a Python int,
    already representable as an unsigned 32-bit value (though on some
    platforms/versions it could be signed, hence the historical `& 0xFFFFFFFF`
    masking). fastcrc exposes the same CRC-32/ISO-HDLC algorithm via
    `fastcrc.crc32.iso_hdlc(payload)`, returning an unsigned integer result.

    This wrapper calls the fastcrc ISO-HDLC variant (the same polynomial and
    reflection/init/xorout parameters zlib.crc32 uses) and masks the result
    to 32 bits with `& 0xFFFFFFFF` so callers get an identical unsigned
    32-bit integer to what zlib.crc32(payload) would have produced.

    zlib.crc32 also supports an optional `value` argument that seeds the
    computation to allow incremental/running checksums across multiple
    calls (`zlib.crc32(data, value)`). fastcrc's crc32 functions have no
    confirmed equivalent seeding parameter, so that usage pattern is not
    supported here: if a caller ever needs a seed, we raise NotImplementedError
    rather than silently producing an incorrect checksum.
    """
    if value is not None:
        raise NotImplementedError(
            "_axiom_crc32_wrapper: fastcrc has no confirmed equivalent to "
            "zlib.crc32's incremental 'value' seed argument; seeded/running "
            "checksums are not supported by this wrapper."
        )
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
