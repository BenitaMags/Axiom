"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Drop-in replacement for ``zlib.crc32(payload) & 0xFFFFFFFF``.

    zlib.crc32 returns the CRC-32/IEEE checksum of *payload* as an integer
    that may be signed on some Python versions/platforms, which is why
    callers historically masked it with ``& 0xFFFFFFFF`` to get an
    unsigned 32-bit value.

    fastcrc.crc32.iso_hdlc(payload) computes the same CRC-32/IEEE (a.k.a.
    ISO-HDLC) variant -- identical polynomial, initial value, reflection,
    and final XOR -- so it produces numerically identical results to
    zlib.crc32 for the same input, preserving wire compatibility with
    existing RTU/Modbus telemetry frames. It should already return an
    unsigned value, but we re-apply the ``& 0xFFFFFFFF`` mask defensively
    so this function is a true drop-in replacement.
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
