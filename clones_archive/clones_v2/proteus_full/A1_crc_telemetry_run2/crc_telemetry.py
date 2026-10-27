"""crc_telemetry -- integrity framing for RTU/Modbus telemetry.

Appends/verifies a CRC-32 trailer on telemetry frames. The CRC-32 backend is the
substitution target: `zlib.crc32` (stdlib) -> `fastcrc` (drop-in, faster).
"""
import struct
import fastcrc

CRC_LEN = 4


def _axiom_crc32_wrapper(payload: bytes) -> int:
    """Compute a CRC-32 checksum, bridging zlib's API to fastcrc's.

    zlib.crc32(payload) returns a CRC-32/ISO-HDLC checksum as an integer
    (already unsigned in Python 3, but historically masked with
    & 0xFFFFFFFF to guarantee an unsigned 32-bit result across versions).

    fastcrc exposes the equivalent algorithm as fastcrc.crc32.iso_hdlc,
    which uses the same polynomial/byte order (CRC-32/ISO-HDLC, aka
    IEEE 802.3) so checksums remain compatible with existing RTU/Modbus
    frames. Its return value is documented as an unsigned integer, but we
    still apply & 0xFFFFFFFF here to preserve the original function's
    contract and guard against any future/alternate implementation that
    might not strictly bound its output to 32 bits.
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
