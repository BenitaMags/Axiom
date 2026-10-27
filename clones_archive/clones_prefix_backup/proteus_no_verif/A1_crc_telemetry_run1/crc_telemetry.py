"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _fastcrc_crc32_wrapper(payload: bytes) -> int:
    """Compute a CRC-32 checksum equivalent to zlib.crc32.

    zlib.crc32(payload) returns an unsigned 32-bit integer using the
    standard CRC-32 algorithm (the "ISO-HDLC" / "PKZIP" variant).

    fastcrc does not expose a `crc32` function matching zlib's calling
    convention; instead it provides `fastcrc.crc32.iso_hdlc(payload)`,
    which implements the same CRC-32 variant zlib uses, and already
    returns an unsigned integer in [0, 2**32).

    This wrapper simply calls fastcrc's iso_hdlc implementation and
    returns its result directly, so callers get the same value they
    would have gotten from `zlib.crc32(payload) & 0xFFFFFFFF`.
    """
    return fastcrc.crc32.iso_hdlc(payload)


def crc32(payload: bytes) -> int:
    """CRC-32 of *payload* as an unsigned 32-bit integer."""
    return _fastcrc_crc32_wrapper(payload)


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
